# TODO

Fill this repository with the reference skeleton of a SQLMesh + DuckDB batch pipeline: the default pattern the `design-spec` skill
(in [python-code-standards](https://github.com/CarlosSolrac/python-code-standards)) points every Standard data pipeline at.

**Source material.** Section 10 (reference skeleton, every file) and section 8 (worked example) of the original draft, verified with
SQLMesh 0.236.3 and DuckDB 1.5.6:

    gh api "repos/CarlosSolrac/python-code-standards/contents/handoff/design-spec/SKILL.draft.md?ref=eb189bb" -H "Accept: application/vnd.github.raw"

The draft wins on behaviour; this file wins where they differ. Differences already decided (do not reopen these; log a decision
only for choices this file leaves open):

- **Tool configuration** comes from python-code-standards' baseline (already applied by `setup-standards.sh`), not the draft's
  narrower `pyproject.toml`. Fix whatever the baseline's checks fire on; never suppress Ruff `S608`.
- **`CsvSource`** checks `table_name` (and its schema) against an allowlist read from `external_models.yaml` before building any DDL,
  and a test covers the rejection. It reads the file with `ruamel.yaml` (`YAML(typ="safe")`, a declared dependency), never with PyYAML
  (not installed) or SQLMesh's internal `sqlmesh.utils.yaml`.
- **Diagrams** use `flowchart` with boundary subgraphs, never `C4Context`/`C4Container` (Mermaid's C4 is experimental, lays out poorly,
  and ignores `classDef`).
- **Dependencies** were pinned in daytime (2026-10-17): `duckdb==1.5.6`, `sqlmesh==0.236.2` and `ruamel.yaml==0.19.1`, the newest
  releases at least two weeks old. The draft was verified on SQLMesh 0.236.3, so tonight's done-checks re-verify it on 0.236.2;
  ADR-0001 names 0.236.2. Tonight adds and upgrades nothing. Until task 2 imports all three packages, deptry reports them as unused
  (DEP002); that is expected.
- **Package name** is `project` (`src/project`, the `project` command), as in the draft; projects made from the template rename it.
- **`pyproject.toml`** was finished in daytime (2026-10-17): `[build-system]` (`uv_build`), `[project.scripts] project =
  "project.main:main"` and `src/project/__init__.py`, so `uv sync` installs `project` in editable mode and tests import it from there.
  `uv run project` fails until task 2 adds `src/project/main.py`; that is expected. Tonight changes no tool configuration: if a check
  needs a `pyproject.toml` change, the task writes no code that relies on it and ends blocked, naming the exact change.
- **`.gitignore`** (approved): task 1 adds `data/`, `.cache/` and `logs/`, where SQLMesh keeps the warehouse, its cache and its logs.
  No other `.gitignore` change.
- **Protected files** (`pyproject.toml`, `suppressions.toml`, `.pre-commit-config.yaml`, `.github/workflows/`, `tools/hooks/`,
  `tools/check_*.py`, `tools/changes.py`, `tools/review_brief.py`, `tools/mutation.sh`, `.claude/settings*.json`): read them with the
  Read tool only; never name one in a shell command, and keep each shell command to one purpose. The project's guard hook
  (`tools/hooks/guard_protected.py`) asks for approval on any command that names one alongside `rm`, `cp`, `mv`, `tee`, `sed -i`,
  `git checkout` or `git restore`, and one unanswered prompt stalls the night.
- **One branch, one PR.** This overrides nightshift's one branch and PR per task. All five tasks build on the branch
  `nightshift/sqlmesh-skeleton`, in one worktree (`<repo-parent>/template-sqlmesh-duckdb.nightshift/sqlmesh-skeleton`), with one
  draft PR titled "SQLMesh + DuckDB reference skeleton". The first task to run creates the worktree from `origin/main`, pushes, and
  opens the draft PR; each later task reuses the worktree, pushes to the same branch, and adds its own section to the PR body (what
  changed, how it was verified, its decision and blocker entries). Keep the worktree until the last task finishes. Every commit
  message starts with `Task <#>:` and each red-green unit is its own commit, so the history shows how the skeleton grew. A task's
  whole-branch and adversarial Codex reviews use `--base <the commit the task started from>`.
- **Dependencies** are the table below. A task starts once every task it depends on is `done`; `done — reviews pending` counts as
  done. A task whose dependency ends blocked is blocked too.
- **A blocked task** leaves no half-done commits on the shared branch. A task pushes only once it is done. If it ends blocked, it
  moves its commits to `nightshift/<slug>-blocked` and pushes that branch (no PR), resets the shared branch in the worktree to the
  commit the task started from (those commits were never pushed, so no force-push), and names the side branch under *Needs you*.

| # | Task | Slug | Depends on | Done-check |
|---|------|------|------------|------------|
| 1 | SQLMesh project | `sqlmesh-project` | — | `uv run sqlmesh test` passes |
| 2 | Python package | `python-package` | 1 | project checks pass, including the end-to-end tests of the three runs |
| 3 | Decision records | `adrs` | — | both ADRs exist and follow the §6 template |
| 4 | Worked example | `design-doc` | 1, 2, 3 | Mermaid validator exits 0; no `C4Context`/`C4Container` |
| 5 | README | `readme` | 3, 4 | every relative link resolves |

Order: 1, 3, 2, 4, 5.

1. **SQLMesh project.** `config.yaml`, `external_models.yaml`, `models/staging/stg_orders.sql`, `models/marts/fct_daily_revenue.sql`,
   `audits/assert_positive_amount.sql`, `tests/test_models.yaml`, as in the draft, plus the `.gitignore` entries above. Plain DuckDB
   SQL only: no Jinja, no macros except `@this_model` in audits. Done-check: `uv run sqlmesh test` passes. The PR's CI fails deptry
   (DEP002) until task 2's commits are on the branch; that is expected, not a blocker.
2. **Python package.** `src/project/ports.py` (Protocols only), `src/project/adapters/csv_source.py` (with the allowlist),
   `src/project/main.py` (composition root: ingest, `plan()`, then restate everything downstream of the raw tables), and
   `tests/test_adapters.py`, plus a small sample CSV under `tests/fixtures/`. `main.py` gets unit tests with fakes injected, and the
   three runs are automated as end-to-end tests (`tests/test_pipeline.py`) against a copy of the project in `tmp_path`, so CI keeps
   proving them: a full run from an empty warehouse exits 0; a negative amount in the input makes the run exit 1 and the mart keeps its
   last good rows; a same-day rerun with new rows picks them up. Done-check: the project's own checks pass (Ruff, type checkers,
   pytest with 100% statement and branch coverage, including those end-to-end tests), and `uv run project`, with the sample CSV copied
   to `data/orders.csv`, exits 0. The pre-commit hook's deptry check refuses any commit touching `.py` files until the code imports
   `duckdb`, `sqlmesh` and `ruamel.yaml`, and skipping hooks is not allowed. So task 2's first commit lands only once its code imports
   all three; until then each reviewed unit stays uncommitted in the worktree, and each still gets its working-tree Codex review. Never
   use `SKIP` or `--no-verify`.
3. **Decision records.** `docs/adr/ADR-0001-sqlmesh-with-exit-path.md` and `docs/adr/ADR-0002-replace-and-restate.md`, from the draft.
   Done-check: both exist and follow the ADR template in `design-spec`'s §6.
4. **Worked example.** `docs/design.md`: this repository documented by `design-spec`'s document mode (Standard), following the skill as
   it stands on python-code-standards' `main`. Done-check:
   `node ~/.claude/skills/design-spec/tools/validate_mermaid.mjs docs/design.md` exits 0, and `git grep -n "C4Context\|C4Container"`
   finds nothing.
5. **README.** What the template is, how to start a project from it
   (`gh repo create <name> --template CarlosSolrac/template-sqlmesh-duckdb --clone`), how to run it, and links to `docs/design.md` and
   `docs/adr/`. Done-check: every relative link resolves.

## Morning steps (for the user, not nightshift)

1. Read the log's *Morning summary* and *Needs you*; mark the draft PR ready and merge it with a merge commit or a rebase, not a
   squash, so the per-task commits survive.
2. In `template-0-summaries`, change this template's status from "Being built" to "Ready".
3. Delete this file once every task is merged.
