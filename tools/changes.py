"""Git helpers for checks that look only at what a change adds.

Commands go through the Stop hook's runner, so output is decoded as UTF-8 and
every git failure raises ``GitError``: the checks fail closed rather than read a
broken repository as "nothing changed".
"""

from __future__ import annotations

import re
from collections.abc import Callable

from .hooks.stop_gate import GitError, Result, Runner, git_paths

HUNK: re.Pattern[str] = re.compile(r"^@@ -\S+ \+(\d+)")


def base_commit(base: str | None, run: Runner) -> str:
    """Return the commit to compare against: ``HEAD``, or the merge base with ``base``.

    The merge base, not ``base`` itself, so commits the base branch gained after
    this branch forked are not counted as this branch's changes. Before the first
    commit there is no ``HEAD``; the result is then "", meaning every line is new.
    ``rev-parse --verify --quiet`` exits 1 for that case alone; any other failure
    (a broken configuration, no repository) raises, so it is never mistaken for it.

    Raises:
        GitError: git failed, or could not find the merge base.
    """
    command: tuple[str, ...]
    result: Result
    if base is None:
        command = ("git", "rev-parse", "--verify", "--quiet", "HEAD")
        result = run(command)
        if result.returncode in {0, 1}:
            return "HEAD" if result.returncode == 0 else ""
        raise GitError(command, result)
    command = ("git", "merge-base", base, "HEAD")
    result = run(command)
    if result.returncode != 0:
        raise GitError(command, result)
    return result.output.strip()


def changed_files(commit: str, run: Runner) -> list[str]:
    """Return the files changed since ``commit`` that still exist, sorted."""
    return sorted(git_paths(("git", "diff", "-z", "--name-only", "--relative", "--diff-filter=d", commit), run))


def added_lines(path: str, commit: str, run: Runner, read: Callable[[str], str]) -> list[tuple[int, str]]:
    """Return the ``(line number, text)`` pairs added to ``path`` since ``commit``.

    Every line is added when there is no commit yet (``commit`` is "") or the file
    is untracked; git has no diff for either.
    """
    if not commit or git_paths(("git", "ls-files", "-z", "--others", "--exclude-standard", "--", path), run):
        return list(enumerate(read(path).splitlines(), start=1))
    command: tuple[str, ...] = ("git", "diff", "-U0", "--no-color", "--no-ext-diff", commit, "--", path)
    result: Result = run(command)
    if result.returncode != 0:
        raise GitError(command, result)
    added: list[tuple[int, str]] = []
    number: int = 0
    line: str
    for line in result.output.splitlines():
        hunk: re.Match[str] | None = HUNK.match(line)
        if hunk is not None:
            number = int(hunk.group(1))
        elif line.startswith("+") and not line.startswith("+++"):
            added.append((number, line[1:]))
            number += 1
    return added
