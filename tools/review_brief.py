"""Print a one-screen brief that says where a reviewer should look in a change.

Every section is computed, not judged: the change's size, gate files it touches,
dependency changes, suppressions it adds (approved or not), the most complex
changed functions, changed modules no test imports, and surviving mutants in
changed modules. An empty section says so, so a brief of "none"s means nothing
needs a closer look.

The Stop hook shows it to the user after every turn whose changes pass the gates.

Usage:
    uv run python -m tools.review_brief                    (working tree against HEAD)
    uv run python -m tools.review_brief --base origin/main (this branch's changes)
Exit status is 1 when git fails, otherwise 0.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .changes import added_lines, base_commit
from .check_suppressions import Allowed, allowed_from_text, comments_by_line, suppressions_in
from .hooks.guard_protected import is_protected
from .hooks.stop_gate import PYTHON_SUFFIXES, GitError, Result, Runner, git_paths, run_command, unrecorded_modules

COMPLEXITY_REPORT: str = ".complexipy_cache/review-brief.json"
DEFAULT_COMPLEXITY_CAP: int = 15
TOP_FUNCTIONS: int = 5


@dataclass(frozen=True)
class FileChange:
    """One changed file: lines added and removed, or binary."""

    path: str
    added: int
    removed: int
    binary: bool = False
    untracked: bool = False


def read_text(path: str) -> str:
    """Return a file's text, replacing bytes that are not UTF-8."""
    return Path(path).read_text(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None, run: Runner = run_command, read: Callable[[str], str] = read_text) -> int:
    """Print the brief for the working tree, or for the branch with ``--base``.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.
        run: Executes git and complexipy; replaced by a fake in tests.
        read: Returns a file's text and raises ``FileNotFoundError`` for a missing one; replaced in tests.

    Returns:
        1 when git fails, otherwise 0.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", help="describe the changes since the merge base with this ref instead of HEAD")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        print(build_brief(args.base, run, read))
    except GitError as error:
        print(f"`{' '.join(error.command)}` failed, so there is no brief:\n{error.result.output}")
        return 1
    return 0


def build_brief(base: str | None, run: Runner, read: Callable[[str], str]) -> str:
    """Return the brief as Markdown."""
    commit: str = base_commit(base, run)
    since: str = base or "HEAD"
    changes: list[FileChange] = file_changes(commit, run, read)
    if not changes:
        return f"Review brief: no changes since {since}."
    sources: dict[str, str] = readable_python(changes, read)
    sections: list[str] = [
        size_section(changes),
        gates_section(changes),
        dependencies_section(changes, commit, run, read),
        suppressions_section(sources, commit, run, read),
        complexity_section(sources, run, read),
        coverage_section(sources, read),
        mutation_section(sources, read),
    ]
    return "\n\n".join([f"# Review brief: changes since {since}", *sections])


def file_changes(commit: str, run: Runner, read: Callable[[str], str]) -> list[FileChange]:
    """Return the tracked changes since ``commit`` and the untracked files, in path order."""
    changes: list[FileChange] = []
    new_paths: set[str]
    if commit:
        changes = numstat(commit, run)
        new_paths = git_paths(("git", "ls-files", "-z", "--others", "--exclude-standard"), run)
    else:
        new_paths = git_paths(("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"), run)
    path: str
    for path in new_paths:
        changes.append(FileChange(path, len(read(path).splitlines()), 0, untracked=True))
    return sorted(changes, key=lambda change: change.path)


def numstat(commit: str, run: Runner) -> list[FileChange]:
    """Return ``git diff --numstat`` against ``commit``: tracked files, staged or not."""
    command: tuple[str, ...] = ("git", "diff", "-z", "--numstat", "--no-renames", "--relative", commit)
    result: Result = run(command)
    if result.returncode != 0:
        raise GitError(command, result)
    changes: list[FileChange] = []
    entry: str
    for entry in result.output.split("\0"):
        if entry:
            added: str
            removed: str
            path: str
            added, removed, path = entry.split("\t", 2)
            binary: bool = added == "-"
            changes.append(FileChange(path, 0 if binary else int(added), 0 if binary else int(removed), binary=binary))
    return changes


def readable_python(changes: list[FileChange], read: Callable[[str], str]) -> dict[str, str]:
    """Return the text of each changed Python file that still exists."""
    sources: dict[str, str] = {}
    change: FileChange
    for change in changes:
        if change.path.endswith(PYTHON_SUFFIXES):
            try:
                sources[change.path] = read(change.path)
            except FileNotFoundError:
                continue
    return sources


def size_section(changes: list[FileChange]) -> str:
    """Return the size section: every file with its lines added and removed."""
    lines: list[str] = [f"## Size: {len(changes)} files, +{sum(change.added for change in changes)} / -{sum(change.removed for change in changes)}"]
    change: FileChange
    for change in changes:
        if change.binary:
            lines.append(f"- `{change.path}` binary")
        else:
            lines.append(f"- `{change.path}` +{change.added} / -{change.removed}" + (" (new, untracked)" if change.untracked else ""))
    return "\n".join(lines)


def gates_section(changes: list[FileChange]) -> str:
    """Return the changed files that define a quality gate; they always need a human look."""
    gates: list[str] = [change.path for change in changes if is_protected(change.path)]
    if not gates:
        return "## Gate files touched: none"
    return "\n".join([f"## Gate files touched: {len(gates)}", *(f"- `{path}`" for path in gates)])


def dependencies_section(changes: list[FileChange], commit: str, run: Runner, read: Callable[[str], str]) -> str:
    """Return the requirements added to or removed from ``pyproject.toml``."""
    if "pyproject.toml" not in {change.path for change in changes}:
        return "## Dependencies: no change"
    before_result: Result = run(("git", "show", f"{commit}:./pyproject.toml"))
    before: set[str] = requirements(before_result.output) if commit and before_result.returncode == 0 else set()
    try:
        after: set[str] = requirements(read("pyproject.toml"))
    except FileNotFoundError:
        after = set()
    added: list[str] = sorted(after - before)
    removed: list[str] = sorted(before - after)
    if not added and not removed:
        return "## Dependencies: no change"
    return "\n".join([f"## Dependencies: +{len(added)} / -{len(removed)}", *(f"- added `{item}`" for item in added), *(f"- removed `{item}`" for item in removed)])


def requirements(text: str) -> set[str]:
    """Return every requirement in ``[project] dependencies`` and ``[dependency-groups]``."""
    document: dict[str, object] = tomllib.loads(text)
    found: set[str] = set()
    project: object = document.get("project")
    if isinstance(project, dict):
        found.update(str(item) for item in project.get("dependencies", []))
    groups: object = document.get("dependency-groups")
    if isinstance(groups, dict):
        group: object
        for group in groups.values():
            if isinstance(group, list):
                found.update(str(item) for item in group if isinstance(item, str))
    return found


def suppressions_section(sources: dict[str, str], commit: str, run: Runner, read: Callable[[str], str]) -> str:
    """Return the suppressions the change adds, unapproved ones first."""
    try:
        allowed: Allowed = allowed_from_text(read("suppressions.toml"), "suppressions.toml")
    except (FileNotFoundError, ValueError):
        allowed = Allowed(set())
    found: list[tuple[bool, str, int, str]] = []
    path: str
    source: str
    for path, source in sources.items():
        found.extend(added_suppressions(path, source, added_lines(path, commit, run, read), allowed))
    if not found:
        return "## Suppressions added: none"
    found.sort()
    unapproved: int = sum(1 for approved, *_ in found if not approved)
    lines: list[str] = [f"## Suppressions added: {len(found)} ({unapproved} unapproved)"]
    lines.extend(f"- `{path}:{number}` `{label}` {'approved' if approved else 'UNAPPROVED'}" for approved, path, number, label in found)
    return "\n".join(lines)


def added_suppressions(path: str, source: str, lines: list[tuple[int, str]], allowed: Allowed) -> list[tuple[bool, str, int, str]]:
    """Return ``(approved, path, line, label)`` for each suppression on one file's added lines."""
    comments: dict[int, str] | None = comments_by_line(source) if path.endswith(".py") else None
    return [(allowed.permits(path, suppression), path, number, suppression.label()) for number, text in lines for suppression in suppressions_in(text if comments is None else comments.get(number, ""))]


def complexity_section(sources: dict[str, str], run: Runner, read: Callable[[str], str]) -> str:
    """Return the most complex functions in the changed modules, against the cap."""
    modules: list[str] = sorted(path for path in sources if path.endswith(".py"))
    if not modules:
        return "## Complexity: no changed Python modules"
    # --ignore-complexity: complexipy otherwise exits 1 on an over-cap function, which is
    # exactly the function the reviewer needs to see; a non-zero exit then means it failed.
    if run(("uv", "run", "--no-sync", "complexipy", "--ignore-complexity", "--output-format", "json", "--output", COMPLEXITY_REPORT, *modules)).returncode != 0:
        return "## Complexity: not measured (complexipy did not run)"
    report: list[dict[str, object]] = json.loads(read(COMPLEXITY_REPORT))
    scored: list[tuple[int, str, str]] = sorted(
        ((int(str(entry["complexity"])), str(entry["path"]).replace("\\", "/"), str(entry["function_name"])) for entry in report if str(entry["path"]).replace("\\", "/") in sources),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    lines: list[str] = [f"## Complexity: highest in changed files (cap {complexity_cap(read)})"]
    lines.extend(f"- `{path}` `{name}` {score}" for score, path, name in scored[:TOP_FUNCTIONS])
    return "\n".join(lines)


def complexity_cap(read: Callable[[str], str]) -> int:
    """Return complexipy's configured cap, or its documented default."""
    try:
        settings: object = tomllib.loads(read("pyproject.toml")).get("tool", {}).get("complexipy")
    except FileNotFoundError:
        return DEFAULT_COMPLEXITY_CAP
    cap: object = settings.get("max-complexity-allowed") if isinstance(settings, dict) else None
    return cap if isinstance(cap, int) else DEFAULT_COMPLEXITY_CAP


def coverage_section(sources: dict[str, str], read: Callable[[str], str]) -> str:
    """Return the changed modules that coverage.xml has no record of."""
    try:
        report: str = read("coverage.xml")
    except FileNotFoundError:
        return "## Coverage: no coverage.xml (run the tests with --cov)"
    missing: list[str] = unrecorded_modules(sorted(sources), report)
    if not missing:
        return "## Coverage: every changed module has a coverage record"
    noun: str = "module" if len(missing) == 1 else "modules"
    return "\n".join([f"## Coverage: {len(missing)} changed {noun} without a coverage record", *(f"- `{path}`" for path in missing)])


def mutation_section(sources: dict[str, str], read: Callable[[str], str]) -> str:
    """Return the surviving mutants, from the last mutation run, in the changed modules."""
    try:
        survivors: str = read("mutants/survivors.txt")
    except FileNotFoundError:
        return "## Mutation: no mutants/survivors.txt (run bash tools/mutation.sh)"
    counts: Counter[str] = Counter()
    line: str
    for line in survivors.splitlines():
        # mutmut results lists every mutant not killed; a timeout was caught, so count survivors only.
        if not line.rstrip().endswith(": survived"):
            continue
        # "pkg.mod.x_func__mutmut_3: survived" or "pkg.mod.xǁClassǁmethod__mutmut_3: survived".
        # The mangled name is the last segment, and a module may itself be named x_….
        method: tuple[str, str, str] = line.strip().partition(".xǁ")
        module: str = method[0] if method[1] else line.strip().rpartition(".x_")[0]
        path: str = module.replace(".", "/") + ".py"
        if path in sources:
            counts[path] += 1
    if not counts:
        return "## Mutation: no survivors in changed modules (from the last mutation run)"
    return "\n".join([f"## Mutation: {counts.total()} survivors in changed modules (from the last mutation run)", *(f"- `{path}` {count}" for path, count in sorted(counts.items()))])


if __name__ == "__main__":
    raise SystemExit(main())
