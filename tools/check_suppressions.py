"""Fail when a change adds a suppression the user has not approved.

Suppressing a lint, type, or coverage finding needs the user's explicit
authorization. This makes that rule mechanical: a ``noqa``, ``type: ignore``,
``pyright: ignore``, ``pragma: no cover``, or ``pragma: no branch`` comment on an
added line fails, as does a file- or function-level one (``ruff: noqa``,
``flake8: noqa``, ``mypy:`` and ``pyright:`` settings, ``complexipy: ignore``),
unless ``suppressions.toml`` lists its file and code (for a setting, its whole
value):

    [[suppression]]
    path = "tools/hooks/stop_gate.py"
    code = "S603"
    reason = "argv comes from a fixed list, never a shell"

The guard hook protects ``suppressions.toml``, so an agent adding an entry
triggers the user's permission prompt; that prompt is the approval. A blanket
suppression has no code and can never be approved. One exception is built in:
unit tests may suppress Pyright's ``reportPrivateUsage`` per line.

Only added lines count, so existing suppressions never block an unrelated edit.

Usage:
    uv run python -m tools.check_suppressions <files>         (pre-commit: added since HEAD)
    uv run python -m tools.check_suppressions --base origin/main   (CI: added on this branch)
Exit status is 1 when any unapproved suppression is found or git fails.
"""

from __future__ import annotations

import argparse
import io
import re
import tokenize
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .changes import added_lines, base_commit, changed_files
from .hooks.stop_gate import PYTHON_SUFFIXES, GitError, Runner, run_command

ALLOW_LIST: Path = Path("suppressions.toml")
REQUIRED_KEYS: tuple[str, ...] = ("path", "code", "reason")
# Line-level directives, then the file- and function-level ones (ruff and flake8
# file noqa, mypy and pyright file comments, complexipy's ignore), which silence
# more than a line. coverage.py honors "no cover" and "no branch" pragmas, and
# mutmut skips lines marked "no mutate".
DIRECTIVE: re.Pattern[str] = re.compile(r"#\s*(ruff:\s*noqa|flake8:\s*noqa|noqa|type:\s*ignore|pyright:\s*ignore|pyright:|mypy:|complexipy:\s*ignore|pragma:?\s*no\s*(?:cover|branch|mutate))", re.IGNORECASE)
# Ruff separates noqa codes with commas, spaces, or both; every code is captured.
NOQA_CODES: re.Pattern[str] = re.compile(r":\s*([A-Z]+[0-9]+(?:[\s,]+[A-Z]+[0-9]+)*)")
CODE: re.Pattern[str] = re.compile(r"[A-Z]+[0-9]+")
# Each directive by its letters alone, so "type:ignore", "type: ignore", and
# "pragma no cover" classify the same way their tools read them.
CANONICAL: dict[str, str] = {
    "ruffnoqa": "ruff: noqa",
    "flake8noqa": "flake8: noqa",
    "noqa": "noqa",
    "typeignore": "type: ignore",
    "pyrightignore": "pyright: ignore",
    "pyright": "pyright:",
    "mypy": "mypy:",
    "complexipyignore": "complexipy: ignore",
    "pragmanocover": "pragma: no cover",
    "pragmanobranch": "pragma: no branch",
    "pragmanomutate": "pragma: no mutate",
}
NOQA_KINDS: frozenset[str] = frozenset({"noqa", "ruff: noqa", "flake8: noqa"})
BRACKETED_RULES: re.Pattern[str] = re.compile(r"\[([^\]]*)\]")


@dataclass(frozen=True)
class Suppression:
    """One suppressed code; a comment naming several codes yields one each."""

    kind: str
    code: str

    def label(self) -> str:
        """Return the suppression as it reads in source."""
        if self.kind in NOQA_KINDS:
            return f"{self.kind}: {self.code}" if self.code else f"{self.kind} ({'blanket' if self.kind == 'noqa' else 'whole file'})"
        if self.kind in {"pragma", "complexipy", "mypy", "pyright"}:
            return f"{self.kind}: {self.code}"
        return f"{self.kind}[{self.code}]" if self.code else self.kind


@dataclass(frozen=True)
class Allowed:
    """The approved ``(path, code)`` pairs from the allow-list."""

    pairs: set[tuple[str, str]]

    def permits(self, path: str, suppression: Suppression) -> bool:
        """Return whether this suppression in this file is approved."""
        if suppression.kind == "pyright: ignore" and suppression.code == "reportPrivateUsage" and is_test(path):
            return True
        return bool(suppression.code) and (path, suppression.code) in self.pairs


def is_test(path: str) -> bool:
    """Return whether a path names a unit-test file."""
    normalized: str = "/" + path.replace("\\", "/")
    return "/tests/" in normalized or normalized.rpartition("/")[2].startswith("test_")


def suppressions_in(line: str) -> list[Suppression]:
    """Return every suppression a source line carries, one per code."""
    found: list[Suppression] = []
    directive: re.Match[str]
    for directive in DIRECTIVE.finditer(line):
        found.extend(parse_directive(CANONICAL[re.sub(r"[\s:]", "", directive.group(1).lower())], line[directive.end() :]))
    return found


def parse_directive(word: str, tail: str) -> list[Suppression]:
    """Return the suppressions one directive carries, given its normalized word and the text after it."""
    if word in NOQA_KINDS:
        codes: re.Match[str] | None = NOQA_CODES.match(tail)
        return [Suppression(word, code) for code in (CODE.findall(codes.group(1)) if codes else [""])]
    if word in {"type: ignore", "pyright: ignore"}:
        rules: re.Match[str] | None = BRACKETED_RULES.match(tail)
        return [Suppression(word, rule.strip()) for rule in (rules.group(1).split(",") if rules else [""])]
    if word in {"pyright:", "mypy:"}:
        # The whole setting is the code, so approving one value never approves another.
        return [Suppression(word.removesuffix(":"), " ".join(tail.split()))]
    if word == "complexipy: ignore":
        return [Suppression("complexipy", "ignore")]
    return [Suppression("pragma", word.removeprefix("pragma: "))]


def load_allowed(path: Path) -> Allowed:
    """Read the allow-list; a missing file approves nothing.

    Raises:
        ValueError: an entry lacks a non-empty path, code, or reason.
    """
    if not path.exists():
        return Allowed(set())
    return allowed_from_text(path.read_text(encoding="utf-8"), str(path))


def allowed_from_text(text: str, source: str) -> Allowed:
    """Parse allow-list text; ``source`` names it in error messages.

    Raises:
        ValueError: an entry lacks a non-empty path, code, or reason.
    """
    entries: object = tomllib.loads(text).get("suppression")
    pairs: set[tuple[str, str]] = set()
    index: int
    entry: object
    for index, entry in enumerate(entries if isinstance(entries, list) else [], start=1):
        fields: list[object] = [entry.get(key) for key in REQUIRED_KEYS] if isinstance(entry, dict) else []
        if not fields or not all(isinstance(field, str) and field.strip() for field in fields):
            message: str = f"{source}: [[suppression]] entry {index} needs a non-empty path, code, and reason"
            raise ValueError(message)
        pairs.add((str(fields[0]), str(fields[1])))
    return Allowed(pairs)


def read_file(path: str) -> str:
    """Return a file's text."""
    return Path(path).read_text(encoding="utf-8")


def main(argv: list[str] | None = None, run: Runner = run_command, read: Callable[[str], str] = read_file, allow_list: Path = ALLOW_LIST) -> int:
    """Report every unapproved suppression the change adds.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.
        run: Executes git; replaced by a fake in tests.
        read: Returns a file's text; replaced in tests.
        allow_list: The approved suppressions.

    Returns:
        1 when any unapproved suppression is found or git fails, otherwise 0.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=None, help="compare against the merge base with this ref instead of HEAD")
    parser.add_argument("files", nargs="*", help="files to check; default: every file changed since --base")
    args: argparse.Namespace = parser.parse_args(argv)
    findings: list[str]
    try:
        allowed: Allowed = load_allowed(allow_list)
        commit: str = base_commit(args.base, run)
        files: list[str] = args.files or changed_files(commit, run)
        findings = unapproved(files, commit, run, read, allowed)
    except GitError as error:
        print(f"`{' '.join(error.command)}` failed, so the change cannot be checked:\n{error.result.output}")
        return 1
    except ValueError as error:
        print(error)
        return 1
    finding: str
    for finding in findings:
        print(finding)
    return 1 if findings else 0


def unapproved(files: list[str], commit: str, run: Runner, read: Callable[[str], str], allowed: Allowed) -> list[str]:
    """Return one message per unapproved suppression on an added line of a Python file.

    In a module only real comments are scanned, so directive text inside a string
    or docstring is not a suppression. A notebook (JSON) or a module Python cannot
    tokenize (mid-edit) is scanned as text, which errs toward reporting.
    """
    findings: list[str] = []
    path: str
    for path in files:
        if not path.endswith(PYTHON_SUFFIXES):
            continue
        lines: list[tuple[int, str]] = added_lines(path, commit, run, read)
        comments: dict[int, str] | None = comments_by_line(read(path)) if lines and path.endswith(".py") else None
        number: int
        text: str
        for number, text in lines:
            findings.extend(
                f"{path}:{number}: new suppression `{suppression.label()}` needs the user's approval. Fix the code instead, or ask the user to approve a [[suppression]] entry (path, code, reason) in suppressions.toml."
                for suppression in suppressions_in(text if comments is None else comments.get(number, ""))
                if not allowed.permits(path, suppression)
            )
    return findings


def comments_by_line(source: str) -> dict[int, str] | None:
    """Return each line's comment text by line number, or None when the source cannot be tokenized."""
    try:
        return {token.start[0]: token.string for token in tokenize.generate_tokens(io.StringIO(source).readline) if token.type == tokenize.COMMENT}
    except (tokenize.TokenError, SyntaxError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
