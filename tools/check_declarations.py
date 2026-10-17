"""Enforce the house rule that every variable is annotated before its first binding.

No existing linter checks this: Ruff's ANN rules cover signatures, not local
bindings. This walks each scope in source order and reports any name that is
bound before it carries an annotation.

Instance attributes are covered too: ``self.total = 0`` requires ``total: int`` in
the class body. Dataclass and Pydantic model fields satisfy this by construction,
since their fields *are* class-body annotations.

Exempt, because the language provides no way to annotate them: comprehension and
generator targets, ``except ... as`` names, walrus bindings, imports, function and
class definitions, function parameters (annotated in the signature), and enum
members (the typing spec forbids annotating them). An enum is a class whose base
is an enum class defined earlier in the same file, or resolves through the file's
imports to a standard-library enum base (``enum.Enum``, ``enum.StrEnum``, ...) or
to a base listed in ``pyproject.toml``. List enum bases defined in other modules,
your own or a library's, by their fully qualified import names:

    [tool.check-declarations]
    enum-bases = ["myproject.base.BaseStatus", "django.db.models.TextChoices"]

Relative imports resolve against the file's package, found by walking up through
directories that hold an ``__init__.py``.

Usage:
    uv run python -m tools.check_declarations src tests
Exit status is 1 when any violation is found.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_STDLIB_ENUM_BASES: frozenset[str] = frozenset({"enum.Enum", "enum.IntEnum", "enum.StrEnum", "enum.Flag", "enum.IntFlag", "enum.ReprEnum"})
"""Enum base classes the ``enum`` module exports; members of their subclasses can't be annotated."""

PYPROJECT: Path = Path("pyproject.toml")


@dataclass(frozen=True)
class Violation:
    """A name bound in a scope before it was annotated."""

    path: Path
    line: int
    column: int
    name: str
    hint: str = ""

    def render(self) -> str:
        """Return a single-line, editor-navigable description."""
        message: str = f"{self.path}:{self.line}:{self.column}: {self.name} bound before annotation"
        return f"{message} ({self.hint})" if self.hint else message


class AttributeChecker(ast.NodeVisitor):
    """Flag ``self.<name>`` assignments whose name is not declared in the class body."""

    def __init__(self, path: Path, receiver: str, declared: set[str]) -> None:
        """Initialize the checker.

        Args:
            path: File being checked, used for reporting.
            receiver: Name of the method's first parameter, usually ``self``.
            declared: Attribute names annotated in the class body or an in-file base.
        """
        self.path: Path = path
        self.receiver: str = receiver
        self.declared: set[str] = declared
        self.violations: list[Violation] = []

    def _check(self, node: ast.expr) -> None:
        """Report an attribute target that carries no class-body declaration."""
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == self.receiver:
            if node.attr not in self.declared:
                self.violations.append(Violation(self.path, node.lineno, node.col_offset, f"{self.receiver}.{node.attr}"))
            self.declared.add(node.attr)
            return
        if isinstance(node, (ast.Tuple, ast.List)):
            element: ast.expr
            for element in node.elts:
                self._check(element)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Check every assignment target."""
        target: ast.expr
        for target in node.targets:
            self._check(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """An inline annotation declares the attribute at its first binding."""
        if isinstance(node.target, ast.Attribute) and isinstance(node.target.value, ast.Name) and node.target.value.id == self.receiver:
            self.declared.add(node.target.attr)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        """A loop may bind an attribute directly."""
        self._check(node.target)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Do not descend into nested functions; the receiver may differ."""

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Do not descend into nested async functions."""


class ScopeChecker(ast.NodeVisitor):
    """Check one lexical scope, then recurse into nested scopes."""

    def __init__(self, path: Path, declared: set[str], enum_bases: frozenset[str], package: str) -> None:
        """Initialize the checker.

        Args:
            path: File being checked, used for reporting.
            declared: Names already annotated or otherwise exempt in this scope.
            enum_bases: Fully qualified names of the enum base classes to recognize.
            package: The module's package, against which relative imports resolve.
        """
        self.path: Path = path
        self.declared: set[str] = set(declared)
        self.violations: list[Violation] = []
        self.class_attributes: dict[str, set[str]] = {}
        self.enum_bases: frozenset[str] = enum_bases
        self.package: str = package
        # Fully qualified names of imported bindings, and names bound to in-file enum classes.
        self.imports: dict[str, str] = {}
        self.enum_classes: set[str] = set()
        self.in_enum_body: bool = False
        self.member_hint: str = ""
        # A class body is not an enclosing scope for its methods, so a class-body
        # checker hands its functions the names visible where the class is defined.
        self.visible_to_functions: set[str] = self.declared

    def _bind(self, node: ast.expr) -> None:
        """Record a binding target, reporting it when it lacks a prior annotation."""
        if isinstance(node, ast.Name):
            if node.id not in self.declared and not node.id.startswith("__"):
                self.violations.append(Violation(self.path, node.lineno, node.col_offset, node.id, self.member_hint))
            self.declared.add(node.id)
            return
        if isinstance(node, (ast.Tuple, ast.List)):
            element: ast.expr
            for element in node.elts:
                self._bind(element)
            return
        if isinstance(node, ast.Starred):
            self._bind(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Treat an annotation as declaring the name, with or without a value."""
        if isinstance(node.target, ast.Name):
            self.declared.add(node.target.id)
        if node.value is not None:
            self.visit(node.value)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Check every assignment target; enum members are exempt, since the typing spec forbids annotating them."""
        self.visit(node.value)
        target: ast.expr
        for target in node.targets:
            if not (self.in_enum_body and _is_member_target(target)):
                self._bind(target)

    def visit_For(self, node: ast.For) -> None:
        """Check the loop target, then the body."""
        self.visit(node.iter)
        self._bind(node.target)
        self.visit_body(node.body)
        self.visit_body(node.orelse)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        """Async loops behave identically."""
        self.visit_For(node)  # type: ignore[arg-type]

    def visit_With(self, node: ast.With) -> None:
        """Check each ``as`` target, then the body."""
        item: ast.withitem
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind(item.optional_vars)
        self.visit_body(node.body)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        """Async context managers behave identically."""
        self.visit_With(node)  # type: ignore[arg-type]

    def _bind_pattern(self, node: ast.pattern) -> None:
        """Record every name a structural pattern binds.

        ``MatchAs``, ``MatchStar``, and ``MatchMapping`` rest-targets are ordinary
        bindings and can be pre-declared, so the rule applies to them. Sub-patterns
        bind before the pattern's own name, matching their order in source.
        """
        sub: ast.pattern
        for sub in _sub_patterns(node):
            self._bind_pattern(sub)
        target: ast.Name | None = _pattern_target(node)
        if target is not None:
            self._bind(target)

    def visit_Match(self, node: ast.Match) -> None:
        """Check every case pattern, then each case body."""
        self.visit(node.subject)
        case: ast.match_case
        for case in node.cases:
            self._bind_pattern(case.pattern)
            if case.guard is not None:
                self.visit(case.guard)
            self.visit_body(case.body)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Exempt the handler name; it is unbound at the end of the block."""
        if node.name is not None:
            self.declared.add(node.name)
        self.visit_body(node.body)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        """Exempt walrus bindings, which cannot carry an annotation."""
        self.declared.add(node.target.id)
        self.visit(node.value)

    def _visit_comprehension(self, generators: list[ast.comprehension]) -> None:
        """Check each iterable; the targets are exempt and declare nothing here.

        A comprehension's targets occupy its own scope, so they are never bindings
        of the enclosing scope, and a later binding of the same name still needs
        its own annotation.
        """
        generator: ast.comprehension
        for generator in generators:
            self.visit(generator.iter)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        """Handle list comprehensions."""
        self._visit_comprehension(node.generators)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        """Handle set comprehensions."""
        self._visit_comprehension(node.generators)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        """Handle generator expressions."""
        self._visit_comprehension(node.generators)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        """Handle dict comprehensions."""
        self._visit_comprehension(node.generators)

    def visit_Import(self, node: ast.Import) -> None:
        """Imports bind names that cannot be annotated; each is recorded with the module it names."""
        alias: ast.alias
        for alias in node.names:
            bound: str = (alias.asname or alias.name).split(".")[0]
            self.declared.add(bound)
            self.enum_classes.discard(bound)
            self.imports[bound] = alias.name if alias.asname else bound

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Same treatment as plain imports; a relative import resolves against the file's package."""
        module: str | None = self._absolute_module(node.module, node.level)
        alias: ast.alias
        for alias in node.names:
            bound: str = alias.asname or alias.name
            self.declared.add(bound)
            self.enum_classes.discard(bound)
            if module is None:
                self.imports.pop(bound, None)
            else:
                self.imports[bound] = f"{module}.{alias.name}"

    def _absolute_module(self, module: str | None, level: int) -> str | None:
        """Return the absolute name of an imported module, or None when a relative import leaves the package."""
        if level == 0:
            return module
        parts: list[str] = self.package.split(".") if self.package else []
        if level - 1 >= len(parts):
            return None
        return ".".join([*parts[: len(parts) - level + 1], *([module] if module else [])])

    def visit_Global(self, node: ast.Global) -> None:
        """Names declared global are managed in the module scope."""
        self.declared.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        """Names declared nonlocal are managed in an enclosing scope."""
        self.declared.update(node.names)

    def _enter_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        """Recurse into a function body with its parameters pre-declared.

        The function sees the enclosing scope's declarations, skipping any class
        bodies in between, and the enum names bound so far.
        """
        parameters: set[str] = set()
        arg: ast.arg
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            parameters.add(arg.arg)
        if node.args.vararg is not None:
            parameters.add(node.args.vararg.arg)
        if node.args.kwarg is not None:
            parameters.add(node.args.kwarg.arg)

        inner: ScopeChecker = self._nested(self.visible_to_functions | parameters)
        body: list[ast.stmt] | ast.expr = node.body
        if isinstance(body, list):
            inner.visit_body(body)
        else:
            inner.visit(body)
        self.violations.extend(inner.violations)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """A def binds its own name and opens a new scope."""
        self.declared.add(node.name)
        self._enter_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Async defs bind a name and open a scope, same as sync defs."""
        self.declared.add(node.name)
        self._enter_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Lambdas open a scope with no statements to check."""
        self._enter_function(node)

    def _declared_attributes(self, node: ast.ClassDef) -> set[str]:
        """Collect class-body annotations, including those of in-file base classes.

        Dataclass and Pydantic fields are ordinary class-body annotations, so they
        are picked up here without special-casing either library.
        """
        attributes: set[str] = set()
        base: ast.expr
        for base in node.bases:
            if isinstance(base, ast.Name):
                attributes |= self.class_attributes.get(base.id, set())
        statement: ast.stmt
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                attributes.add(statement.target.id)
        return attributes

    def _nested(self, declared: set[str]) -> ScopeChecker:
        """Return a checker for a scope nested in this one, which sees the imports and enum classes bound so far."""
        inner: ScopeChecker = ScopeChecker(self.path, declared, self.enum_bases, self.package)
        inner.imports = dict(self.imports)
        inner.enum_classes = set(self.enum_classes)
        return inner

    def _qualified_name(self, node: ast.expr) -> str | None:
        """Return the fully qualified name an expression refers to through this scope's imports, or None."""
        if isinstance(node, ast.Attribute):
            owner: str | None = self._qualified_name(node.value)
            return None if owner is None else f"{owner}.{node.attr}"
        if isinstance(node, ast.Name):
            return self.imports.get(node.id)
        return None

    def _is_enum_base(self, base: ast.expr) -> bool:
        """Return whether a base class expression resolves to an enum class in this scope."""
        if isinstance(base, ast.Name) and base.id in self.enum_classes:
            return True
        return self._qualified_name(base) in self.enum_bases

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """A class binds its own name, opens a scope, and declares its attributes; an enum also exempts its members."""
        self.declared.add(node.name)
        attributes: set[str] = self._declared_attributes(node)
        self.class_attributes[node.name] = attributes

        method: ast.stmt
        for method in node.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.args.args:
                receiver: str = method.args.args[0].arg
                attribute_checker: AttributeChecker = AttributeChecker(self.path, receiver, set(attributes))
                body_statement: ast.stmt
                for body_statement in method.body:
                    attribute_checker.visit(body_statement)
                self.violations.extend(attribute_checker.violations)

        is_enum: bool = any(self._is_enum_base(base) for base in node.bases)
        # A violation in a class with an imported base may be an enum member whose
        # base is defined elsewhere; say how to list it.
        imported_bases: list[str] = [] if is_enum else [name for name in map(self._qualified_name, node.bases) if name is not None]
        self.imports.pop(node.name, None)
        if is_enum:
            self.enum_classes.add(node.name)
        else:
            self.enum_classes.discard(node.name)

        inner: ScopeChecker = self._nested(self.visible_to_functions)
        inner.class_attributes = self.class_attributes
        inner.in_enum_body = is_enum
        if imported_bases:
            inner.member_hint = f"if {' or '.join(imported_bases)} is an enum, list it in enum-bases under [tool.check-declarations] in pyproject.toml"
        inner.visible_to_functions = self.visible_to_functions
        inner.visit_body(node.body)
        self.violations.extend(inner.violations)

    def visit_body(self, body: list[ast.stmt]) -> None:
        """Visit statements in source order so declarations precede bindings."""
        statement: ast.stmt
        for statement in body:
            self.visit(statement)


def _is_member_target(node: ast.expr) -> bool:
    """Return whether an assignment target in an enum body binds only members: a name, or a tuple or list of them."""
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(_is_member_target(element) for element in node.elts)
    return isinstance(node, ast.Name)


def _sub_patterns(node: ast.pattern) -> list[ast.pattern]:
    """Return a structural pattern's direct sub-patterns, in source order."""
    if isinstance(node, ast.MatchAs):
        return [] if node.pattern is None else [node.pattern]
    if isinstance(node, (ast.MatchMapping, ast.MatchSequence, ast.MatchOr)):
        return list(node.patterns)
    if isinstance(node, ast.MatchClass):
        return [*node.patterns, *node.kwd_patterns]
    return []


def _pattern_target(node: ast.pattern) -> ast.Name | None:
    """Return the name a pattern binds itself, apart from its sub-patterns, as a reportable node."""
    if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
        return ast.Name(id=node.name, lineno=node.lineno, col_offset=node.col_offset)
    if isinstance(node, ast.MatchMapping) and node.rest is not None:
        # ``**rest`` has no AST node of its own; report it at the end of
        # the mapping so it sorts after the keys it follows in source.
        return ast.Name(id=node.rest, lineno=node.end_lineno or node.lineno, col_offset=node.end_col_offset or node.col_offset)
    return None


def check_source(path: Path, source: str, enum_bases: frozenset[str] = _STDLIB_ENUM_BASES, package: str = "") -> list[Violation]:
    """Return every declaration violation in one module.

    Args:
        path: File path, used for reporting.
        source: Module source text.
        enum_bases: Fully qualified names of the enum base classes to recognize.
        package: The module's package, against which relative imports resolve.

    Returns:
        Violations in source order.
    """
    tree: ast.Module = ast.parse(source, filename=str(path))
    checker: ScopeChecker = ScopeChecker(path, set(), enum_bases, package)
    checker.visit_body(tree.body)
    return sorted(checker.violations, key=lambda item: (item.line, item.column))


def read_source(path: Path) -> str:
    """Return parseable Python source for a module or notebook.

    Notebook code cells are concatenated so bindings in an earlier cell declare
    names used in a later one, matching how the notebook actually executes.

    Args:
        path: A ``.py`` or ``.ipynb`` file.

    Returns:
        Source text ready for ``ast.parse``.
    """
    text: str = path.read_text(encoding="utf-8")
    if path.suffix != ".ipynb":
        return text

    document: dict[str, object] = json.loads(text)
    cells: object = document.get("cells")
    if not isinstance(cells, list):
        return ""
    return "\n".join(_cell_code(cell) for cell in cells if isinstance(cell, dict) and cell.get("cell_type") == "code")


def _cell_code(cell: dict[str, object]) -> str:
    """Return one notebook code cell's source, without IPython magics or shell escapes."""
    lines: object = cell.get("source", "")
    joined: str = "".join(lines) if isinstance(lines, list) else str(lines)
    return "\n".join(line for line in joined.splitlines() if not line.lstrip().startswith(("%", "!")))


def package_of(path: Path) -> str:
    """Return the dotted package a module belongs to, from the ``__init__.py`` files above it."""
    parts: list[str] = []
    directory: Path = path.resolve().parent
    while (directory / "__init__.py").is_file():
        parts.insert(0, directory.name)
        directory = directory.parent
    return ".".join(parts)


def load_enum_bases(pyproject: Path) -> frozenset[str]:
    """Return the standard-library enum bases plus those listed in ``[tool.check-declarations]``.

    Raises:
        ValueError: The file is not valid TOML, or ``enum-bases`` is not a list of strings.
    """
    if not pyproject.is_file():
        return _STDLIB_ENUM_BASES
    # PEP 518 makes ``tool`` and its subtables tables, so only the value is checked.
    configured: object = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("tool", {}).get("check-declarations", {}).get("enum-bases", [])
    if not isinstance(configured, list) or not all(isinstance(item, str) for item in configured):
        msg: str = "[tool.check-declarations] enum-bases must be a list of strings"
        raise ValueError(msg)
    return _STDLIB_ENUM_BASES | frozenset(configured)


def iter_python_files(roots: list[Path]) -> Iterator[Path]:
    """Yield every Python file or notebook under the given files or directories."""
    root: Path
    for root in roots:
        if root.is_file():
            yield root
        else:
            pattern: str
            for pattern in ("*.py", "*.ipynb"):
                yield from sorted(root.rglob(pattern))


def main(argv: list[str] | None = None, pyproject: Path = PYPROJECT) -> int:
    """Run the checker over the given paths.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.
        pyproject: The project file that may list further enum bases.

    Returns:
        1 when any violation is found or the project file is malformed, otherwise 0.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args: argparse.Namespace = parser.parse_args(argv)

    try:
        enum_bases: frozenset[str] = load_enum_bases(pyproject)
    except ValueError as exc:
        print(f"{pyproject}: {exc}", file=sys.stderr)
        return 1

    violations: list[Violation] = []
    path: Path
    for path in iter_python_files(args.paths):
        try:
            violations.extend(check_source(path, read_source(path), enum_bases, package_of(path)))
        except SyntaxError as exc:
            print(f"{path}:{exc.lineno}:{exc.offset}: could not parse: {exc.msg}", file=sys.stderr)
            return 1

    violation: Violation
    for violation in violations:
        print(violation.render())

    if violations:
        print(f"\n{len(violations)} declaration violation(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
