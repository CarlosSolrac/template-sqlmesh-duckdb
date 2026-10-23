# template-sqlmesh-duckdb

The reference skeleton of a SQLMesh + DuckDB batch pipeline: the default pattern that the `design-spec` skill in
[python-code-standards](https://github.com/CarlosSolrac/python-code-standards) points every Standard data pipeline at. A CSV of orders
is landed as a typed raw table, two SQL models turn it into daily revenue, and one composition root runs both steps:

    data/orders.csv → raw.orders → orders.stg_orders → orders.fct_daily_revenue   (all in data/warehouse.duckdb)

Python 3.13, `duckdb==1.5.6`, `sqlmesh==0.236.2` ([ADR-0001](docs/adr/ADR-0001-sqlmesh-with-exit-path.md)), managed with `uv`.

## Start a project from it

```sh
gh repo create <name> --private --template CarlosSolrac/template-sqlmesh-duckdb --clone
cd <name>
```

`gh` needs `--private`, `--public` or `--internal` when given a name. Then rename the Python package and its command, both called
`project` in the template:

1. `git mv src/project src/<package>`
2. In `pyproject.toml`, set `name = "<package>"` under `[project]` and replace `project = "project.main:main"` under
   `[project.scripts]` with `<package> = "<package>.main:main"`.
3. In `tests/`, change `from project.` to `from <package>.`. The package's own modules import each other relatively and need no change.
4. `uv sync`, which updates `uv.lock`.

From then on, `uv run <package>` replaces `uv run project` below.

## Run it

```sh
uv sync                                          # Python 3.13, the pinned dependencies, the dev tools, and the `project` command
mkdir -p data && cp tests/fixtures/orders.csv data/orders.csv   # the input; tests/fixtures/orders.csv is a 4-row sample
uv run project
```

`uv run project` replaces `raw.orders` with `data/orders.csv`, then lets SQLMesh run the model tests, apply the models, and restate
everything downstream of the raw table, so a same-day rerun picks up new rows
([ADR-0002](docs/adr/ADR-0002-replace-and-restate.md)). It exits 0 when every plan applied and 1 when the CSV could not be ingested, an audit failed or a model test failed.
A run builds on a copy of the warehouse and swaps it in only when it succeeds
([ADR-0007](docs/adr/ADR-0007-build-aside-and-swap.md)), so `data/warehouse.duckdb` never holds rows an audit rejected, and a failed
run leaves it unchanged. The copy of the failed run is kept as `data/warehouse.failed-<timestamp>.duckdb` (the newest three are kept); open one
with DuckDB to see the rejected rows. A run works from any directory ([ADR-0006](docs/adr/ADR-0006-run-in-the-project-directory.md)) and creates `data/` if missing.
`data/` (input, warehouse, the failed copies and the in-progress `data/.build`) is git-ignored.

When a run exits 1, its output names the audit that failed (with the model it belongs to) or the model test that failed, or the
ingestion error. To run the model tests alone, on their own fixture rows, use `uv run sqlmesh test` from the repository root. What each
failure leaves behind is in the [error flow](docs/design.md#14-error-flow) and [findings](docs/design.md#12-findings).

## Checks

```sh
uv run pre-commit run --all-files    # Ruff, declarations, pyright and mypy (strict), suppressions, complexity, deptry, gitleaks
uv run pytest --cov --cov-branch     # unit and end-to-end tests; 100% statement and branch coverage required
uv run sqlmesh test                  # SQLMesh model tests (tests/test_models.yaml)
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs these first two, plus diff coverage, the suppression check against the
base branch, and `pip-audit`. After changing a model's `FROM`, regenerate the lineage view with
`uv run sqlmesh dag docs/lineage.html` and commit it with the change.

## Documentation

- [docs/design.md](docs/design.md): the design spec of this repository, written with `design-spec`'s document mode:
  [size](docs/design.md#1-size-and-numbers), [context view](docs/design.md#2-context-view),
  [components](docs/design.md#5-components), [boundary contracts](docs/design.md#6-boundary-contracts),
  [directory tree](docs/design.md#9-directory-tree), [decisions](docs/design.md#10-decisions),
  [non-functional requirements](docs/design.md#11-non-functional-requirements), [findings](docs/design.md#12-findings),
  [error flow](docs/design.md#14-error-flow) and [open questions](docs/design.md#open-questions).
- [docs/adr/](docs/adr/): [ADR-0001](docs/adr/ADR-0001-sqlmesh-with-exit-path.md), SQLMesh with an exit path;
  [ADR-0002](docs/adr/ADR-0002-replace-and-restate.md), replace raw tables and restate downstream on every run;
  [ADR-0003](docs/adr/ADR-0003-planner-port.md) to [ADR-0006](docs/adr/ADR-0006-run-in-the-project-directory.md), the `Planner` port,
  the contract-typed `CsvSource`, rejecting undeclared CSV columns, and running in the project directory;
  [ADR-0007](docs/adr/ADR-0007-build-aside-and-swap.md), build each run on a copy of the warehouse and swap it in on success.
- [docs/lineage.html](docs/lineage.html): SQLMesh's model lineage, generated (it loads its graph library from unpkg, so it needs
  network to view).
