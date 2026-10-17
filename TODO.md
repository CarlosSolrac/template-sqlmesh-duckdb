# TODO

Fill this repository with the reference skeleton of a SQLMesh + DuckDB batch pipeline: the default pattern the `design-spec` skill
(in [python-code-standards](https://github.com/CarlosSolrac/python-code-standards)) points every Standard data pipeline at.

**Source material.** Section 10 (reference skeleton, every file) and section 8 (worked example) of the original draft, verified with
SQLMesh 0.236.3 and DuckDB 1.5.6:

    gh api "repos/CarlosSolrac/python-code-standards/contents/handoff/design-spec/SKILL.draft.md?ref=eb189bb" -H "Accept: application/vnd.github.raw"

The draft wins on behaviour; this file wins where they differ. Differences already decided:

- **Tool configuration** comes from python-code-standards' baseline (already applied by `setup-standards.sh`), not the draft's
  narrower `pyproject.toml`. Fix whatever the baseline's checks fire on; never suppress Ruff `S608`.
- **`CsvSource`** checks `table_name` (and its schema) against an allowlist read from `external_models.yaml` before building any DDL,
  and a test covers the rejection.
- **Diagrams** use `flowchart` with boundary subgraphs, never `C4Context`/`C4Container` (Mermaid's C4 is experimental, lays out poorly,
  and ignores `classDef`).
- **Dependencies** were pinned in daytime (2026-10-17): `duckdb==1.5.6` and `sqlmesh==0.236.2`, the newest releases at least two weeks
  old. The draft was verified on SQLMesh 0.236.3, so tonight's done-checks re-verify it on 0.236.2; ADR-0001 names 0.236.2. Tonight
  adds and upgrades nothing. Until task 2 imports both packages, deptry reports them as unused (DEP002) on `main`; that is expected.
- **Package name** is `project` (`src/project`, the `project` command), as in the draft; projects made from the template rename it.

1. **SQLMesh project.** `config.yaml`, `external_models.yaml`, `models/staging/stg_orders.sql`, `models/marts/fct_daily_revenue.sql`,
   `audits/assert_positive_amount.sql`, `tests/test_models.yaml`, as in the draft. Plain DuckDB SQL only: no Jinja, no macros except
   `@this_model` in audits. Done-check: `uv run sqlmesh test` passes.
2. **Python package.** `src/project/ports.py` (Protocols only), `src/project/adapters/csv_source.py` (with the allowlist),
   `src/project/main.py` (composition root: ingest, `plan()`, then restate everything downstream of the raw tables), and
   `tests/test_adapters.py`, plus a small sample CSV under `tests/fixtures/`. Done-check: the project's own checks pass (Ruff, type
   checkers, pytest with 100% statement and branch coverage); a full run from an empty warehouse exits 0; a negative amount in the input
   makes the run exit 1 and the mart keeps its last good rows; a same-day rerun with new rows picks them up.
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

1. Read the log's *Morning summary* and *Needs you*; mark the draft PRs ready and merge them in dependency order.
2. In `template-0-summaries`, change this template's status from "Being built" to "Ready".
3. Delete this file once every task is merged.
