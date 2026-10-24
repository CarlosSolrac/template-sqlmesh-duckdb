# TODO

Open items only. Each one says what is open, why it matters, and what would close it. A closed item is deleted; its history stays in
git and the pull request that closed it.

1. **Production row volume, growth rate and refresh frequency are unknown.** Both models are `kind FULL` and the raw table is replaced
   on every run ([ADR-0002](docs/adr/ADR-0002-replace-and-restate.md)), and every run copies the whole warehouse file
   ([ADR-0007](docs/adr/ADR-0007-build-aside-and-swap.md)), so run time and disk grow linearly with total rows. Closes when the
   numbers are known and either confirm `FULL` models or call for incremental ones.
2. **Who reads the warehouse, and can they be held to marts only?** Rejected rows never reach the live warehouse, but the
   `data/warehouse.failed-*.duckdb` copies hold them, and nothing in the repository restricts access to them. Closes with a named set
   of readers and, if needed, an access rule for the failed copies.
3. **Failed-copy retention is fixed at three.** The fourth failure deletes the oldest copy and its evidence, and the copies cost up to
   three times the warehouse size in disk (`StagedWarehouse.keep`, `src/project/adapters/staged_warehouse.py:30`). Closes when a
   retention is chosen, by count or by age, for the expected failure rate and warehouse size.
4. **23 mutants survive the test suite** (CI run 40141629867 on 2026-10-24: 219 mutants, 196 killed, none without tests). By
   function: `main.run` 8, `main.main` 6, `FailureLog.log_test_results` 3, `FailureLog.log_failed_models` 2, and one each in
   `csv_source.declared_columns`, `csv_source.safe_loader`, `staged_warehouse.utc_now` and `StagedWarehouse.discard`. Most change
   only a log message's wording, which the tests check by substring, or are equivalent (`utc_now` without UTC is corrected by
   `discard()`'s `astimezone(UTC)`). Closes when each is killed by a test or recorded as equivalent. To list them,
   run `wsl -e bash -lc "bash tools/mutation.sh"`; to see one, run `uv run --no-sync mutmut show <name>` in WSL with
   `UV_PROJECT_ENVIRONMENT=$HOME/.venvs/<checkout folder>`, the environment `tools/mutation.sh` creates.
5. **Upstream: mutation-test the newest python-code-standards tools.** The tool changes merged there after its last mutation pull
   request (#29) have not been mutation-tested: `check_passthrough.py`, the `passthrough: allow` marker, and the async visitors typed
   with unions, two of which are vendored here in `tools/`. That repository has no nightly `mutation.yml` on its default branch.
   Closes with a mutmut run there on those changes and, optionally, a nightly workflow.
