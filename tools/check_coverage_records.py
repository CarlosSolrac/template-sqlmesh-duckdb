"""CI: fail when a Python module changed on this branch has no coverage record.

A module no test imports is never recorded in ``coverage.xml``, and diff-cover
silently skips files without a record, so an entirely untested new module
would pass the 100%-of-changed-lines gate. This closes that gap with the same
check the Stop hook runs locally (``unrecorded_modules``). Notebooks are exempt:
pytest-cov never measures them.

Usage, after the test step has written coverage.xml:
    uv run python -m tools.check_coverage_records --base origin/main
Exit status is 1 when a changed module has no record or git fails.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from .changes import base_commit, changed_files
from .hooks.stop_gate import COVERAGE_XML, GitError, Runner, read_coverage_xml, run_command, unrecorded_modules


def main(argv: list[str] | None = None, run: Runner = run_command, read_coverage: Callable[[Path], str] = read_coverage_xml) -> int:
    """Report every module changed since the merge base that coverage.xml does not record.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.
        run: Executes git; replaced by a fake in tests.
        read_coverage: Returns a coverage report's text; replaced in tests.

    Returns:
        1 when a changed module has no coverage record or git fails, otherwise 0.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="the branch's base ref, e.g. origin/main")
    parser.add_argument("--coverage", type=Path, default=COVERAGE_XML, help="the Cobertura report pytest-cov wrote")
    args: argparse.Namespace = parser.parse_args(argv)
    files: list[str]
    try:
        files = changed_files(base_commit(args.base, run), run)
    except GitError as error:
        print(f"`{' '.join(error.command)}` failed, so the change cannot be checked:\n{error.result.output}")
        return 1
    missing: list[str] = unrecorded_modules(files, read_coverage(args.coverage))
    path: str
    for path in missing:
        print(f"{path}: no coverage record. No test imports this module, so none of its lines are measured and diff-cover skips it. Add a test that exercises it.")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
