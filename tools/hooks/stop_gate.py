"""Stop hook: keep the agent working until the quality gates pass on its changes.

When the working tree has changed Python files (deletions included), this runs
what CI runs, in order: pre-commit on the files that still exist, the test suite
with branch coverage, and diff-cover at 100% of changed lines. The first failure
blocks the stop and hands its output back to the agent, so "done" means the
gates passed. When they pass, the user is shown the review brief
(``tools/review_brief.py``): where in the change to look.

A turn with no changed Python costs nothing: the gates are skipped.

To avoid an endless loop on a failure the agent cannot fix, a stop that is
already a retry (``stop_hook_active``) goes through, with a visible warning to
the user that the work is not verified.

Usage, from ``.claude/settings.json``, run from the project root with the hook
event JSON on stdin:
    uv run --no-sync python tools/hooks/stop_gate.py
Exit status is always 0; the decision is the JSON written to stdout.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

PYTHON_SUFFIXES: tuple[str, ...] = (".py", ".ipynb")
TESTS: tuple[str, ...] = ("uv", "run", "pytest", "-q", "--cov", "--cov-branch", "--cov-report=xml")
REVIEW_BRIEF: tuple[str, ...] = ("uv", "run", "--no-sync", "python", "-m", "tools.review_brief")
CHANGED_LINES: tuple[str, ...] = ("uv", "run", "diff-cover", "coverage.xml", "--compare-branch=main", "--include-untracked", "--fail-under=100")
COVERAGE_XML: Path = Path("coverage.xml")
# The gate tooling setup-standards.sh vendors. It is tested at 100% where it is
# maintained and kept byte-identical there, so a project's tests never import it;
# requiring a coverage record would fail every adoption. A project's own tools
# are not on this list and stay gated.
VENDORED_TOOLS: frozenset[str] = frozenset(
    {
        "tools/__init__.py",
        "tools/changes.py",
        "tools/check_coverage_records.py",
        "tools/check_declarations.py",
        "tools/check_suppressions.py",
        "tools/hooks/__init__.py",
        "tools/hooks/guard_protected.py",
        "tools/hooks/stop_gate.py",
        "tools/review_brief.py",
    }
)
# Each measured file's path, relative to the project root because TESTS runs a bare
# --cov from there. Read by pattern: xml.etree would trip Ruff's S314 for no gain.
COVERAGE_FILENAME: re.Pattern[str] = re.compile(r'<class\b[^>]*\bfilename="([^"]*)"')
# Enough tail for the failing assertion or lint findings, without flooding the agent's context.
MAX_OUTPUT_CHARS: int = 6000


@dataclass(frozen=True)
class Result:
    """A finished command's exit status and its stdout and stderr combined."""

    returncode: int
    output: str


type Runner = Callable[[Sequence[str]], Result]


def run_command(command: Sequence[str]) -> Result:
    """Run a command from the current directory and capture everything it prints.

    Output is decoded as UTF-8, the encoding git uses for paths, rather than the
    locale's (cp1252 on Windows), which would garble non-ASCII file names.
    """
    completed: subprocess.CompletedProcess[str] = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", check=False)  # noqa: S603 -- argv comes from this module's fixed gate list, never a shell
    return Result(completed.returncode, completed.stdout + completed.stderr)


def read_coverage_xml(path: Path = COVERAGE_XML) -> str:
    """Return the coverage report's text, or "" when there is none, which records no file."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def main(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout, run: Runner = run_command, read_coverage: Callable[[], str] = read_coverage_xml) -> int:
    """Read one Stop event, run the gates on changed Python, and block the stop on failure.

    Args:
        stdin: Source of the hook event JSON.
        stdout: Destination of the decision JSON; nothing is written to allow the stop.
        run: Executes one command; replaced by a fake in tests.
        read_coverage: Returns the coverage.xml text the test gate wrote; replaced in tests.

    Returns:
        Always 0, so Claude Code reads the decision from stdout.
    """
    event: dict[str, object] = json.loads(stdin.read())
    failure: tuple[str, Result] | None
    try:
        changes: ChangedPython = changed_python(run)
    except GitError as error:
        # Fail closed: a broken repository is not "nothing changed".
        failure = (" ".join(error.command), error.result)
    else:
        if not changes.existing and not changes.deleted:
            return 0
        failure = first_failure(changes.existing, run, read_coverage)
    if failure is None:
        show_review_brief(stdout, run)
        return 0
    label: str = failure[0]
    output: str = failure[1].output[-MAX_OUTPUT_CHARS:]
    decision: dict[str, object]
    if event.get("stop_hook_active"):
        decision = {"systemMessage": f"GATE FAILED: `{label}` still fails after a retry. Claude stopped anyway; this work is NOT verified."}
    else:
        decision = {
            "decision": "block",
            "reason": f"Quality gate failed: `{label}` (exit {failure[1].returncode}). Fix the cause, never by weakening configuration or adding a suppression, then finish.\n\n{output}",
        }
    stdout.write(json.dumps(decision))
    return 0


@dataclass(frozen=True)
class ChangedPython:
    """The working tree's changed Python files, split by whether they still exist."""

    existing: list[str]
    deleted: list[str]


def changed_python(run: Runner) -> ChangedPython:
    """Return the staged, unstaged, and untracked Python changes, each list sorted.

    A deletion is a change too: removing a module can break what imports it, so
    it triggers the tests even though there is no file left to lint.
    """
    candidates: set[str] = git_paths(("git", "ls-files", "-z", "--modified", "--others", "--exclude-standard"), run)
    candidates |= git_paths(("git", "diff", "-z", "--cached", "--name-only", "--relative", "--diff-filter=d"), run)
    deleted: set[str] = git_paths(("git", "ls-files", "-z", "--deleted"), run)
    deleted |= git_paths(("git", "diff", "-z", "--cached", "--name-only", "--relative", "--diff-filter=D"), run)
    return ChangedPython(
        existing=sorted(path for path in candidates - deleted if path.endswith(PYTHON_SUFFIXES)),
        deleted=sorted(path for path in deleted if path.endswith(PYTHON_SUFFIXES)),
    )


def show_review_brief(stdout: TextIO, run: Runner) -> None:
    """Show the user the review brief for the change that just passed the gates.

    The brief is information for the reviewer, never a gate: when it fails or says
    nothing, the stop goes through silently.
    """
    brief: Result = run(REVIEW_BRIEF)
    if brief.returncode == 0 and brief.output.strip():
        stdout.write(json.dumps({"systemMessage": brief.output.rstrip()}))


class GitError(Exception):
    """A git listing failed, so which files changed is unknown."""

    command: tuple[str, ...]
    result: Result

    def __init__(self, command: Sequence[str], result: Result) -> None:
        """Record the failing command and what git printed."""
        super().__init__(" ".join(command))
        self.command = tuple(command)
        self.result = result


def git_paths(command: Sequence[str], run: Runner) -> set[str]:
    """Return the paths a ``-z`` git listing prints.

    ``-z`` makes git print each path verbatim and NUL-terminated. Without it, git
    quotes any path with non-ASCII or special characters, and the quoted form
    would never match a Python suffix, so the gates would be skipped.

    Raises:
        GitError: git failed, for example outside a repository, on a broken
            configuration, or on a repository owned by another user.
    """
    result: Result = run(command)
    if result.returncode != 0:
        raise GitError(command, result)
    return {path for path in result.output.split("\0") if path}


def first_failure(files: list[str], run: Runner, read_coverage: Callable[[], str]) -> tuple[str, Result] | None:
    """Run each gate in order and return the first failure's label and result.

    pre-commit runs only when files remain to lint; given no files it would run
    nothing useful. Between the tests and diff-cover, every changed module must
    appear in the coverage report: a module no test imports is never recorded,
    and diff-cover silently skips files without a record.
    """
    failure: tuple[str, Result] | None
    if files:
        failure = run_gate(("uv", "run", "pre-commit", "run", "--files", *files), run)
        if failure is not None:
            return failure
    failure = run_gate(TESTS, run)
    if failure is not None:
        return failure
    unrecorded: list[str] = unrecorded_modules(files, read_coverage())
    if unrecorded:
        listing: str = "\n".join(f"  {path}" for path in unrecorded)
        return "coverage record check", Result(1, f"No coverage record for:\n{listing}\nNo test imports these modules, so none of their lines are measured and diff-cover skips them. Add a test that exercises each one.")
    return run_gate(CHANGED_LINES, run)


def run_gate(command: Sequence[str], run: Runner) -> tuple[str, Result] | None:
    """Run one ``uv run <tool> ...`` gate and return its label and result when it fails.

    A failed gate whose tool uv cannot even start is not installed, which no code change
    fixes, so the label and output say so instead of naming the command. Any other
    failure, uv's own included (a malformed ``pyproject.toml``), keeps its diagnostic.
    """
    result: Result = run(command)
    if result.returncode == 0:
        return None
    tool: str = command[2]
    probe: Result = run(("uv", "run", "--no-sync", tool, "--version"))
    if probe.returncode == 0 or f"Failed to spawn: `{tool}`" not in probe.output:
        return " ".join(command), result
    advice: str = f"`{tool}` is not installed in the project environment, so this gate cannot run. Change no code: tell the user to add it with `uv add --dev {tool}`, or to re-run setup-standards.sh with -y --update."
    return f"{tool} (not installed)", Result(result.returncode, f"{result.output}\n\n{advice}")


def unrecorded_modules(files: list[str], report: str) -> list[str]:
    """Return the changed ``.py`` files the coverage report has no record of.

    Notebooks are exempt: pytest-cov never measures them. So is the vendored gate
    tooling (``VENDORED_TOOLS``), which no project test imports.
    """
    recorded: set[str] = {html.unescape(name) for name in COVERAGE_FILENAME.findall(report)}
    return [path for path in files if path.endswith(".py") and path not in recorded and path not in VENDORED_TOOLS]


if __name__ == "__main__":
    raise SystemExit(main())
