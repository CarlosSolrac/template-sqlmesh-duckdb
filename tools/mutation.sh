#!/usr/bin/env bash
#
# mutation.sh -- run mutation testing (mutmut) and list the surviving mutants.
#
# A surviving mutant is a change to the code that no test noticed: the line ran,
# but nothing checked its result. Report-only: survivors never fail this script;
# mutmut errors do.
#
# Run it through bash: setup writes it without the executable bit, and commits
# made on Windows never record one.
#   bash tools/mutation.sh
# mutmut needs os.fork, so it runs on Linux and macOS. On Windows, run it in WSL:
#   wsl -e bash tools/mutation.sh
# Pass a mutant name pattern to narrow a local run:
#   bash tools/mutation.sh 'mypackage.module*'
#
# Results: mutants/mutmut-cicd-stats.json (counts) and mutants/survivors.txt.

set -euo pipefail

# The project root is the nearest directory holding pyproject.toml, which is not
# always the git root.
cd "$(dirname "$0")"
while [ ! -f pyproject.toml ] && [ "$PWD" != / ]; do cd ..; done

# mutmut's own guess uses the checkout folder's name, so it breaks in a clone or a
# CI checkout under another name. Require the code to be listed explicitly.
if ! grep -q '^source_paths *=' pyproject.toml; then
    printf 'mutation.sh: list the code to mutate in pyproject.toml, e.g.\n  [tool.mutmut]\n  source_paths = ["mypackage"]\n' >&2
    exit 2
fi

# Keep the Linux environment outside the checkout: under WSL this folder is shared
# with Windows, and syncing here would replace the Windows .venv.
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/$(basename "$PWD")}"

uv sync --all-groups --quiet
uv run --no-sync mutmut run "$@"
uv run --no-sync mutmut export-cicd-stats
uv run --no-sync mutmut results | tee mutants/survivors.txt
