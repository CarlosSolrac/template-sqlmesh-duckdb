# Design: Orders pipeline

Mode: **Document**, size **Standard**, derived from the code at `a8a9a5b` on 2026-10-18 with design-spec's document mode. Items
marked *inferred* (in diagrams: a label ending in ` ?` with a dashed border) were deduced from the code; confirm or correct them.
`path:line` references are relative to the repository root. Decisions cited as Dn are recorded in the bodies of
[PR #1](https://github.com/CarlosSolrac/template-sqlmesh-duckdb/pull/1) (D2) and
[PR #2](https://github.com/CarlosSolrac/template-sqlmesh-duckdb/pull/2) (D4–D13).

This repository is the reference skeleton of a SQLMesh + DuckDB batch pipeline: a CSV of orders is landed as a typed raw table, two
SQL models turn it into daily revenue, and one composition root runs both steps.

## 1. Size and numbers

- Classification: **Standard**. Three layers (ingestion adapter, SQLMesh models, composition root) around two Protocols, in one
  process. Pipeline source: 228 lines of Python in `src/project/` and 39 lines of SQL in `models/` and `audits/`, plus 14 lines of
  SQLMesh configuration (`config.yaml`, `external_models.yaml`). Tests: 314 lines of Python and 25 of YAML. `tools/` holds 1,599 lines of
  vendored python-code-standards checks, which are not part of the pipeline. Counted with
  `git ls-files -z -- '<dir>/*.py' '<dir>/*.sql' | xargs -0 cat | wc -l`.
- External systems: two, the input file drop (`data/orders.csv`) and the DuckDB warehouse file (`data/warehouse.duckdb`), so the spec
  has a context view (D12).
- Volume: sample data only, 4 raw rows in `tests/fixtures/orders.csv:2-5` (3 orders over 2 days). Production row volume, growth rate
  and refresh frequency are unknown (open question 1).
- What the numbers would decide: both models are `kind FULL` (`models/staging/stg_orders.sql:3`, `models/marts/fct_daily_revenue.sql:3`)
  and the raw table is replaced on every run ([ADR-0002](adr/ADR-0002-replace-and-restate.md)), so each run reads the whole history.
  Run time grows linearly with total rows; ADR-0002 names incremental models as the change once the numbers call for it.

## 2. Context view

```mermaid
flowchart LR
    Operator["Operator: starts a run, reads its exit status"]
    Upstream[["Upstream export: drops the orders file ?"]]:::inferred
    Warehouse[("DuckDB file data/warehouse.duckdb: keeps raw, staging and mart tables between runs")]
    Consumers["Mart consumers: read marts only ?"]:::inferred
    subgraph System["Orders pipeline (one Python process)"]
        Pipeline["project: land the raw orders, then plan and restate the models"]
    end
    Operator -->|"uv run project; exit 0 or 1"| Pipeline
    Upstream -->|"data/orders.csv"| Pipeline
    Pipeline -->|"raw.orders, orders.stg_orders, orders.fct_daily_revenue"| Warehouse
    Warehouse -->|"orders.fct_daily_revenue"| Consumers
    classDef inferred stroke:#555,stroke-dasharray:5 5
```

The `sqlmesh` CLI (`uv run sqlmesh plan ...`) opens the same warehouse file to diagnose a failed run; it is a second way in, not a
second process of the pipeline. Paths are relative to the project directory, which `main()` makes the working directory for the run
(`src/project/main.py:29`, D9).

## 3. Container view

Not applicable: the pipeline is one process (container views are for Large systems).

## 4. Domain states

*Inferred* from the adapter, the models and the audits. Concepts: an **order** (key `order_id`; a new `ordered_at` is a new version
of it) and a **daily revenue** row (key `order_date`).

```mermaid
stateDiagram-v2
    [*] --> Received: row in data/orders.csv
    Received --> Refused: CsvSource refuses the file (undeclared table or column, wrong type, missing file)
    Refused --> [*]: previous raw.orders kept, nothing planned
    Received --> Landed: CsvSource replaces raw.orders in one transaction
    Landed --> Superseded: a later version of the same order_id exists
    Superseded --> [*]
    Landed --> Staged: latest version per order_id, typed, ordered_at converted to UTC
    Staged --> Blocked: audit fails (null key, duplicate order_id, amount not positive)
    Blocked --> Received: source corrected and reloaded
    Staged --> Aggregated: counted in its order_date row of fct_daily_revenue
    Aggregated --> [*]
```

A run blocked by a staging audit leaves the failing rows in `orders.stg_orders` and the mart at its last good rows (section 12, allowed
exceptions); a run blocked by a mart audit leaves them in the mart (finding 2). An order with a NULL `amount` reaches *Aggregated*
without failing any audit (finding 1).

## 5. Components

```mermaid
flowchart LR
    subgraph Orchestration
        Main["main: run inside the project dir; choose CsvSource and sqlmesh.Context"]
        Run["run: ingest, release the file, plan, restate the raw tables; PlanError means exit 1"]
    end
    subgraph Ingestion
        Csv[["CsvSource: load only tables and columns external_models.yaml declares; replace in one transaction"]]
    end
    subgraph Transformation
        Context[["sqlmesh.Context: test, apply and audit the models"]]
        Stg[("orders.stg_orders: latest version per order_id, cast, UTC; audits null keys, unique order_id")]
        Fct[("orders.fct_daily_revenue: one row per order_date; audits null columns, unique order_date")]
    end
    subgraph Assurance
        PositiveAudit["assert_positive_amount: block amount not positive"]
        ModelTests["test_models.yaml: block a plan whose model output differs from expected rows"]
    end
    subgraph Storage
        Raw[("raw.orders: columns typed as external_models.yaml declares")]
    end
    RawSource{{"RawSource port"}}
    Planner{{"Planner port"}}
    Main -->|"list[RawSource], connect, Planner factory"| Run
    Run -.-> RawSource
    Csv -.->|implements| RawSource
    Run -.-> Planner
    Context -.->|satisfies| Planner
    Run -->|"DuckDBPyConnection"| Csv
    Csv -->|"raw.orders"| Raw
    Run -->|"plan, then plan restating raw.orders"| Context
    ModelTests -->|"expected rows"| Context
    Context -->|"rebuild"| Stg
    Raw -->|"raw.orders"| Stg
    Stg -->|"orders.stg_orders"| Fct
    Stg -->|"orders.stg_orders"| PositiveAudit
```

| node | source |
|---|---|
| Main | `src/project/main.py:19-34`: working directory `src/project/main.py:29`, creates `data/` `src/project/main.py:31`, chooses `CsvSource` `src/project/main.py:33` and `Context` `src/project/main.py:34` |
| Run | `src/project/main.py:37-58`; `ingest` closes the connection before SQLMesh opens the file `src/project/main.py:61-70`; `transform` plans, then restates `src/project/main.py:73-85`; `PlanError` → 1 `src/project/main.py:55-57` |
| Csv | `src/project/adapters/csv_source.py:16-53`: declared tables and their column types `src/project/adapters/csv_source.py:68-83`, `<schema>.<table>` names only `src/project/adapters/csv_source.py:86-96`, undeclared columns refused `src/project/adapters/csv_source.py:40-43`, one transaction `src/project/adapters/csv_source.py:44-52` |
| RawSource | `src/project/ports.py:9-19` |
| Planner | `src/project/ports.py:22-35`; `sqlmesh.Context` passed where a `Planner` is expected, `src/project/main.py:34` |
| Context | `config.yaml:1-8` (DuckDB at `data/warehouse.duckdb`, relative to the working directory, D9); runs `tests/` before every plan, SQLMesh's default (`skip_tests=False`) |
| Stg | `models/staging/stg_orders.sql:1-18`: grain `models/staging/stg_orders.sql:4`, audits `models/staging/stg_orders.sql:5-9`, casts `models/staging/stg_orders.sql:13-16`, latest version `models/staging/stg_orders.sql:18` |
| Fct | `models/marts/fct_daily_revenue.sql:1-16`: grain `models/marts/fct_daily_revenue.sql:4`, audits `models/marts/fct_daily_revenue.sql:5-8`, casts `models/marts/fct_daily_revenue.sql:12-14` |
| PositiveAudit | `audits/assert_positive_amount.sql:1-5`, attached at `models/staging/stg_orders.sql:8` |
| ModelTests | `tests/test_models.yaml:1-25` |
| Raw | `external_models.yaml:1-6` |

## 6. Boundary contracts

| boundary | input | output | grain/key | invariants | owner | source |
|---|---|---|---|---|---|---|
| Command line | `uv run project` | exit status | one run | 0 when both plans applied; 1 on `PlanError` (a failing audit or model test), the mart at its last good rows unless its own audit failed (finding 2); an ingestion error ends the run with a traceback (finding 4) | `main` | `pyproject.toml:16-17`, `src/project/main.py:19-58` |
| Ingestion | `data/orders.csv` with a header row | `raw.orders`: `order_id INTEGER`, `customer_id INTEGER`, `amount DECIMAL(12, 2)`, `ordered_at TIMESTAMPTZ` | one row per CSV row; raw rows before deduplication, no key | table declared in `external_models.yaml` as `<schema>.<table>` of plain identifiers; no undeclared column (D8); declared columns present with their types; replaced, never appended, in one transaction, so a failed load keeps the previous rows | `CsvSource` | `src/project/adapters/csv_source.py:37-52`, `src/project/adapters/csv_source.py:68-96`, `external_models.yaml:1-6` |
| `RawSource` port | `duckdb.DuckDBPyConnection` | the raw table replaced; its row count as `int` | one call per raw table | `table_name` is read-only (`@property`); the table's schema matches `external_models.yaml` | composition root | `src/project/ports.py:9-19` |
| `Planner` port | `restate_models: Iterable[str] \| None`, `no_prompts`, `auto_apply` | the plan applied to prod, or `PlanError` | one plan per call | model tests run before planning; a failing audit raises `PlanError` and skips downstream models; `close()` releases the warehouse file | composition root (`sqlmesh.Context` satisfies it, D4) | `src/project/ports.py:22-35`, `src/project/main.py:73-85` |
| Staging | `raw.orders` | `orders.stg_orders` | one row per order, keyed by `order_id` (the version with the latest `ordered_at`) | every column cast; `not_null(order_id, customer_id, ordered_at_utc)`; `unique_values(order_id)`; `assert_positive_amount` (rows with `amount <= 0` fail; NULL passes, finding 1) | `stg_orders` | `models/staging/stg_orders.sql:1-18`, `audits/assert_positive_amount.sql:1-5` |
| Mart | `orders.stg_orders` | `orders.fct_daily_revenue` | one row per UTC day, keyed by `order_date` | every column cast; `not_null(order_date, order_count, revenue)`; `unique_values(order_date)` | `fct_daily_revenue` | `models/marts/fct_daily_revenue.sql:1-16` |

No HTTP API, so no OpenAPI file.

## 7. Dependency matrix

Not applicable: three modules carry code (`main`, `ports`, `adapters.csv_source`; the two `__init__.py` files hold only a docstring),
and the matrix starts above four. Import direction: `main → adapters.csv_source, ports` (`src/project/main.py:12-13`); `ports` and
`adapters.csv_source` import no internal module (`CsvSource` satisfies `RawSource` structurally). Third-party imports (`duckdb`,
`sqlmesh`, `ruamel.yaml`; `src/project/main.py:8-10`, `src/project/ports.py:6`, `src/project/adapters/csv_source.py:8-9`) are exactly
the declared runtime dependencies (`pyproject.toml:10-14`), which deptry checks on every commit (`.pre-commit-config.yaml:43-49`).
Below the matrix threshold, the direction needs no import-linter contract.

## 8. Model lineage

Not applicable: two models (the DAG starts above four); the component flowchart shows the lineage `raw.orders → orders.stg_orders →
orders.fct_daily_revenue` (`models/staging/stg_orders.sql:17`, `models/marts/fct_daily_revenue.sql:15`). No Python models.

SQLMesh's own view of the same lineage is [lineage.html](lineage.html) (models only; it loads its graph library from unpkg, so it needs
network to view), generated with `uv run sqlmesh dag docs/lineage.html` (D15). It is generated output: rerun the command whenever a
model's `FROM` changes, in the same commit.

## 9. Directory tree

```text
.
├── config.yaml                      # SQLMesh: DuckDB gateway at data/warehouse.duckdb, dialect duckdb
├── external_models.yaml             # raw table contract: names and column types (CsvSource's allowlist)
├── models/
│   ├── staging/stg_orders.sql       # latest version per order, typed, UTC; audited
│   └── marts/fct_daily_revenue.sql  # daily totals; audited
├── audits/assert_positive_amount.sql
├── src/project/
│   ├── main.py                      # composition root: main(), run(), ingest(), transform()
│   ├── ports.py                     # Protocols only: RawSource, Planner
│   ├── adapters/csv_source.py       # CsvSource: the only SQL built in Python (identifier-only DDL)
│   └── py.typed
├── tests/
│   ├── test_models.yaml             # SQLMesh unit tests, one per model
│   ├── test_adapters.py             # CsvSource against in-memory DuckDB
│   ├── test_main.py                 # run() with fake sources and a fake planner
│   ├── test_pipeline.py             # end-to-end runs of main() on a copy of the project
│   └── fixtures/orders.csv          # sample input
├── docs/
│   ├── design.md                    # this file
│   ├── lineage.html                 # generated: uv run sqlmesh dag docs/lineage.html
│   └── adr/                         # decision records (section 10)
├── data/                            # git-ignored: orders.csv (input), warehouse.duckdb
└── tools/                           # vendored python-code-standards checks, not part of the pipeline
```

## 10. Decisions

| record | what it covers |
|---|---|
| [ADR-0001](adr/ADR-0001-sqlmesh-with-exit-path.md) | SQLMesh for the transformations, pinned to `sqlmesh==0.236.2`; models stay plain DuckDB SQL (no Jinja; macros only `@this_model` in audits and the incremental time-range macros), which is the exit path to another runner |
| [ADR-0002](adr/ADR-0002-replace-and-restate.md) | adapters replace raw tables; the composition root applies model changes with `plan()`, then restates everything downstream of the raw tables, so same-day reruns pick up new rows; revisit for incremental loads |

Decisions taken while building the skeleton, recorded only in pull request bodies (finding 5):

| record | what it covers |
|---|---|
| D2, [PR #1](https://github.com/CarlosSolrac/template-sqlmesh-duckdb/pull/1) | staging audits kept as in design-spec's draft, so a NULL `amount` passes (finding 1) |
| D4, [PR #2](https://github.com/CarlosSolrac/template-sqlmesh-duckdb/pull/2) | the `Planner` Protocol, which `sqlmesh.Context` satisfies, so `run()` takes fakes in unit tests |
| D5, PR #2 | `CsvSource(table_name, path, contract)` takes its column types from `external_models.yaml` |
| D6, PR #2 | `main(project_dir=PROJECT_DIR) -> int` as the console script; no `__main__` block |
| D8, PR #2 | `CsvSource` refuses CSV columns the contract does not declare |
| D9, PR #2 | `main()` runs inside the project directory, because SQLMesh resolves the warehouse path against the working directory |
| D10, PR #2 | sibling-relative imports inside `src/project`, because deptry does not see the package as first-party |

## 11. Non-functional requirements

Only requirements a check verifies.

| ID | Requirement | Target | Verified by |
|----|-------------|--------|-------------|
| NFR-01 | Rows that fail a staging audit never reach the mart (a NULL amount fails none, finding 1) | a failing staging audit makes the run exit 1; `orders.fct_daily_revenue` keeps its last good rows | `tests/test_pipeline.py:59-66` |
| NFR-02 | Same-day reruns process new data | a new row on an already-computed day appears in the mart | `tests/test_pipeline.py:69-76` |
| NFR-03 | A run from an empty warehouse completes | exit 0; mart equals the sample's daily totals | `tests/test_pipeline.py:50-56` |
| NFR-04 | The run does not depend on the caller's working directory | a run started outside the project, or with a relative project path, uses the project's warehouse | `tests/test_pipeline.py:34`, `tests/test_pipeline.py:79-83` |
| NFR-05 | Only declared raw tables and columns are loaded | `ValueError` before any DDL; no schema created | `tests/test_adapters.py:58-112` |
| NFR-06 | A failed reload keeps the previous raw table | previous rows after a missing file or a value of the wrong type | `tests/test_adapters.py:40-55` |
| NFR-07 | Models produce the expected rows | `uv run sqlmesh test`: 2 tests pass; SQLMesh also runs them before every plan, so each end-to-end test runs them in CI | `tests/test_models.yaml:1-25`, `tests/test_pipeline.py:30-31` |
| NFR-08 | Tests cover the code | 100% of statements and branches, whole project and changed lines | `pyproject.toml:175-182`, `.github/workflows/ci.yml:40-49` |
| NFR-09 | Strict static typing | pyright and mypy, both strict, pass | `.pre-commit-config.yaml:18-31`, `.github/workflows/ci.yml:34-35` |
| NFR-10 | No dependency with a known vulnerability | `pip-audit` passes | `.github/workflows/ci.yml:56-57` |

## 12. Findings

1. **Rule 5, a NULL amount passes every audit (known).** `not_null` in `models/staging/stg_orders.sql:6` omits `amount`, and
   `amount <= 0` in `audits/assert_positive_amount.sql:5` is unknown for NULL. Consequence: such an order counts in `order_count` but
   adds nothing to `revenue` (`models/marts/fct_daily_revenue.sql:13-14`), silently, unless every order that day lacks an amount, which
   makes `revenue` NULL and fails the mart's `not_null`. Fix: add `amount` to `stg_orders`' `not_null` columns, here and in design-spec's
   draft. (Known: D2, PR #1.)
2. **Rule 5, a failing mart audit leaves its failing rows in the mart (new).** Restating a `FULL` model rebuilds its prod table in
   place, so when an audit of `orders.fct_daily_revenue` (`models/marts/fct_daily_revenue.sql:5-8`) fails, the mart already holds the
   failing rows, while the run logs that the marts keep their last good rows (`src/project/main.py:56`; also the docstring at
   `src/project/main.py:47`). Reproduced on 2026-10-18 with SQLMesh 0.236.2: after a good run, a day whose only order has no amount
   makes the run exit 1 and leaves `(2026-10-03, 1, NULL)` in the mart. Consequence: consumers, who read marts only, see a row a
   blocking audit rejected, and the log tells the operator otherwise. Fix: enforce every mart invariant upstream in staging, so a mart
   audit can fail only on a model bug (finding 1's fix closes the case found); make the message say which table keeps the failing
   rows; add an end-to-end test of a failing mart audit.
3. **Rule 5, tied versions deduplicate arbitrarily (known).** `models/staging/stg_orders.sql:18` orders versions by `ordered_at` only,
   so two versions of an order with the same `ordered_at` and different amounts keep whichever row comes first. Consequence: revenue
   can depend on input row order. Fix: a tie-breaking key, or an audit that rejects tied versions; first in design-spec's draft. (Known:
   task 1's adversarial review, PR #1.)
4. **Rule 5, ingestion failures are not caught once per unit of work (new).** `run()` catches only `PlanError`
   (`src/project/main.py:52-57`); `CsvSource.load` raises `ValueError` or `duckdb.Error` (`src/project/adapters/csv_source.py:32-35`),
   which propagate (`tests/test_main.py:110-119`). Consequence: an undeclared column or a malformed CSV ends `uv run project` with a
   traceback rather than one logged line, and failures come in two families, not one. Fix: a project exception (for example
   `IngestionError`) that adapters raise and `run()` logs before returning 1.
5. **Rule 12, decisions without records (new).** D4, D5, D8 and D9 (section 10) shape the code but live only in PR #2's body, not in
   `docs/adr/`. Consequence: a reader of the repository alone cannot see why, and could undo D9's `contextlib.chdir`
   (`src/project/main.py:29`) and bring back the two-warehouses bug it fixes. Fix: an ADR for each decision kept after review.
6. **Rule 11, a model change does not require a test change (new).** Each model has a unit test that checks every output column
   (`tests/test_models.yaml:1-25`), but no check fails a change under `models/` that leaves `tests/test_models.yaml` untouched. The
   staging test also uses only `+00` timestamps (`tests/test_models.yaml:6-7`), so the UTC conversion (`models/staging/stg_orders.sql:16`)
   is never exercised with another offset. Consequence: a model edit can ship with a stale test that still passes. Fix: a CI step that
   compares changed paths, and a test row with a non-zero offset.

Allowed exceptions and rules that do not apply:

- Rule 5: after a staging audit fails, `orders.stg_orders` keeps the failing rows (`src/project/main.py:77-78`); the rule allows it because
  consumers read marts only (open question 2).
- Rule 8: `CsvSource` interpolates identifiers into DDL (`src/project/adapters/csv_source.py:46-47`) only after checking the name
  against `external_models.yaml` and the `<schema>.<table>` pattern (`src/project/adapters/csv_source.py:13`,
  `src/project/adapters/csv_source.py:37-38`); tested at `tests/test_adapters.py:69-96`.
- Rule 3: `main()` creates the `data/` directory (`src/project/main.py:31`) and sets the working directory for the run
  (`src/project/main.py:29`); both are wiring, and it writes no file contents itself.
- Rule 6: no import-linter contract, which the rule needs only when the dependency matrix applies (section 7).
- Rule 7: no Python models.
- Rule 14: the only other diagram, `docs/lineage.html`, is generated from the models (section 8); the ADRs have none.

## 13. Class diagram

Not applicable: no Python contract is under review; the two Protocols are in section 6.

## 14. Error flow

The failure paths are the template's main promise (NFR-01, NFR-05, NFR-06), so they are drawn. `tests/test_main.py:77-119` drives the
same paths with fake sources and a fake planner; `tests/test_pipeline.py:50-83` drives them end to end.

```mermaid
sequenceDiagram
    participant Main as main
    participant Run as run
    participant Csv as CsvSource
    participant DB as data/warehouse.duckdb
    participant SQLMesh as sqlmesh.Context
    Main->>Run: sources, connect, open_planner
    Run->>Csv: load(con)
    alt undeclared table or column, wrong type, missing file
        Csv-->>Run: ValueError or duckdb.Error, raw.orders unchanged
        Run->>DB: close the ingestion connection
        Run-->>Main: the exception propagates, nothing is planned
    else loaded
        Csv->>DB: replace raw.orders in one transaction
        Csv-->>Run: row count
        Run->>DB: close the ingestion connection
        Run->>SQLMesh: plan, then plan restating raw.orders
        Note over SQLMesh: model tests run before each plan
        alt a model test fails
            SQLMesh-->>Run: PlanError before anything is applied, both models keep their rows
        else a staging audit fails
            SQLMesh-->>Run: PlanError, stg_orders keeps the failing rows, the mart keeps its last good rows
        else a mart audit fails
            SQLMesh-->>Run: PlanError, the mart keeps the failing rows (finding 2)
        else every test and audit passes
            SQLMesh->>DB: rebuild orders.stg_orders and orders.fct_daily_revenue
        end
        Run->>SQLMesh: close()
        Run-->>Main: 1 after PlanError, else 0
    end
```

## 15. Change impact

Not applicable: document mode describes the code as it is; the change-impact diagram is for restructuring in design mode.

## Open questions

1. What are the production row volume, growth rate and refresh frequency? They decide between `FULL` models with replaced raw tables
   (ADR-0002) and incremental ones.
2. Who reads the warehouse, and can they be held to marts only? After a blocked run, `orders.stg_orders` (or, finding 2, the mart)
   holds the failing rows, and nothing in the repository restricts access to it.
3. Should findings 1 and 3 be fixed here first, or in design-spec's draft, which this template follows on behaviour (D2)?
