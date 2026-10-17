"""PreToolUse hook: ask the user before a tool call changes a quality gate.

Tool configuration, the pre-commit and CI pipelines, the gate scripts, and the
hook settings decide what "passing" means. An agent that edits them can make a
failing check pass without fixing anything, so each such edit needs the user's
explicit approval. The hook answers "ask" rather than "deny": a legitimate
change is one approval away, an illegitimate one is never silent.

Shell commands are matched by pattern, so the shell check is best effort; CI,
which runs the unmodified gates, remains the backstop.

Usage, from ``.claude/settings.json``, with the hook event JSON on stdin. It runs
in the project environment, whatever the current directory:
    uv run --no-sync --project "$CLAUDE_PROJECT_DIR" python "$CLAUDE_PROJECT_DIR/tools/hooks/guard_protected.py"
Exit status is always 0; the decision is the JSON written to stdout.
"""

from __future__ import annotations

import json
import re
import sys
from typing import TextIO, cast

PROTECTED_NAMES: frozenset[str] = frozenset(
    {
        "pyproject.toml",
        "ruff.toml",
        ".ruff.toml",
        "setup.cfg",
        "mypy.ini",
        "pyrightconfig.json",
        "pytest.ini",
        "tox.ini",
        ".coveragerc",
        ".pre-commit-config.yaml",
        "suppressions.toml",
    }
)
PROTECTED_SUFFIXES: tuple[str, ...] = (
    "/tools/check_declarations.py",
    "/tools/check_suppressions.py",
    "/tools/check_coverage_records.py",
    "/tools/changes.py",
    "/tools/mutation.sh",
    "/tools/review_brief.py",
    "/.claude/settings.json",
    "/.claude/settings.local.json",
)
PROTECTED_DIRECTORIES: tuple[str, ...] = ("/.github/workflows/", "/tools/hooks/")
FILE_TOOLS: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit"})
SHELL_TOOLS: frozenset[str] = frozenset({"Bash", "PowerShell"})

# Every protected file as it can appear in a command. One alternation serves both
# patterns below, so a redirect is checked against exactly the files that are named.
PROTECTED_TARGETS: str = (
    "(?:"
    + "|".join(re.escape(name) for name in sorted(PROTECTED_NAMES))
    + r"|\.github[/\\]workflows|tools[/\\](?:check_declarations|check_suppressions|check_coverage_records|changes|review_brief)\.py|tools[/\\]mutation\.sh|tools[/\\]hooks|\.claude[/\\]settings)"
)
# A protected file named anywhere in a command: a bare name, or a path ending in one.
PROTECTED_IN_COMMAND: re.Pattern[str] = re.compile(r"(?:^|[\s'\"=/\\])" + PROTECTED_TARGETS, re.IGNORECASE)
# Commands that write, move, or delete the files they name. A redirect only
# counts when it targets a protected file, so "2>/dev/null" stays silent.
WRITES_IN_COMMAND: re.Pattern[str] = re.compile(
    r"\bsed\b[^|;&\n]*\s(?:-[a-zA-Z]*i|--in-place)"
    r"|>>?\s*['\"]?[^\s|;&]*" + PROTECTED_TARGETS + r"|\b(?:tee|mv|cp|rm|truncate)\b"
    r"|\bgit\s+(?:checkout|restore)\b"
    r"|\b(?:Set-Content|Add-Content|Out-File|Remove-Item|Move-Item|Copy-Item)\b",
    re.IGNORECASE,
)
DEPENDENCY_CHANGE: re.Pattern[str] = re.compile(r"\buv\s+(?:add|remove)\b|\buv\s+lock\b[^|;&\n]*--upgrade")


def main(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """Read one PreToolUse event and write an "ask" decision when it changes a gate.

    Args:
        stdin: Source of the hook event JSON.
        stdout: Destination of the decision JSON; nothing is written to allow the call.

    Returns:
        Always 0, so Claude Code reads the decision from stdout.
    """
    event: dict[str, object] = json.loads(stdin.read())
    tool_input: dict[str, object] = cast("dict[str, object]", event.get("tool_input", {}))
    reason: str | None = protected_reason(str(event.get("tool_name", "")), {key: str(value) for key, value in tool_input.items()})
    if reason is not None:
        decision: dict[str, object] = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": reason}}
        stdout.write(json.dumps(decision))
    return 0


def protected_reason(tool_name: str, tool_input: dict[str, str]) -> str | None:
    """Return why this tool call needs the user's approval, or None when it does not."""
    if tool_name in FILE_TOOLS:
        path: str = tool_input.get("file_path", "")
        if is_protected(path):
            return f"{path} defines a quality gate. Changing it can make a failing check pass without a fix, so the user must approve. If the goal is a passing check, fix the code instead."
        return None
    if tool_name in SHELL_TOOLS:
        return command_reason(tool_input.get("command", ""))
    return None


def is_protected(path: str) -> bool:
    """Return whether a file path, POSIX or Windows, names a protected file.

    Matching ignores case: on Windows and macOS ``PyProject.toml`` *is* the
    protected file. On a case-sensitive filesystem the cost is an extra prompt.
    """
    # The leading "/" anchors the suffix and directory checks for a relative path.
    normalized: str = "/" + path.replace("\\", "/").casefold()
    name: str = normalized.rpartition("/")[2]
    return name in PROTECTED_NAMES or normalized.endswith(PROTECTED_SUFFIXES) or any(directory in normalized for directory in PROTECTED_DIRECTORIES)


def command_reason(command: str) -> str | None:
    """Return why a shell command needs the user's approval, or None when it does not."""
    if DEPENDENCY_CHANGE.search(command):
        return "This command changes the project's dependencies, which needs the user's approval. Ask before adding, removing, or upgrading a package."
    if PROTECTED_IN_COMMAND.search(command) and WRITES_IN_COMMAND.search(command):
        return "This command appears to modify a file that defines a quality gate, which needs the user's approval. If the goal is a passing check, fix the code instead."
    return None


if __name__ == "__main__":
    raise SystemExit(main())
