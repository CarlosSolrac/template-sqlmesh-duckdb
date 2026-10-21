"""Composition root: the only place concrete adapters are chosen and the pipeline is run."""

import contextlib
import logging
from collections.abc import Callable, Sequence
from pathlib import Path

import duckdb
from sqlmesh import Context
from sqlmesh.core.console import set_console
from sqlmesh.utils.errors import PlanError

from .adapters.csv_source import CsvSource
from .adapters.errors import IngestionError
from .diagnostics import FailureLog
from .ports import Planner, RawSource

logger: logging.Logger = logging.getLogger(__name__)
PROJECT_DIR: Path = Path(__file__).resolve().parents[2]


def main(project_dir: Path = PROJECT_DIR) -> int:
    """Wire the adapters and run the pipeline on the SQLMesh project in `project_dir`; return the exit status.

    The run happens with `project_dir` as the working directory, because SQLMesh resolves `config.yaml`'s relative warehouse path
    against it, and ingestion must write to the same file. A relative `project_dir` is resolved against the caller's working directory.
    Only this module logs at INFO; the root logger stays at WARNING, so SQLMesh does not log every statement it runs.
    """
    logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
    logger.setLevel(logging.INFO)
    root: Path = project_dir.resolve()
    set_console(FailureLog())
    with contextlib.chdir(root):
        data_dir: Path = root / "data"
        data_dir.mkdir(exist_ok=True)
        warehouse: Path = data_dir / "warehouse.duckdb"
        sources: list[RawSource] = [CsvSource(table_name="raw.orders", path=data_dir / "orders.csv", contract=root / "external_models.yaml")]
        return run(sources, lambda: duckdb.connect(warehouse), lambda: Context(paths=str(root)))


def run(sources: Sequence[RawSource], connect: Callable[[], duckdb.DuckDBPyConnection], open_planner: Callable[[], Planner]) -> int:
    """Ingest every source, then apply model changes and restate everything downstream of the raw tables.

    Args:
        sources: The ingestion adapters, each replacing one raw table.
        connect: Opens the warehouse for ingestion; the connection is closed before `open_planner` is called, because SQLMesh needs
            the DuckDB file to itself.
        open_planner: Opens SQLMesh on the warehouse.

    Returns:
        0 when every plan applied; 1 when a source could not be ingested (nothing is planned) or a plan failed; after a failed audit, the model that failed it keeps the rows that failed and the models downstream of it keep their last good rows, while a failed model test applies nothing.

    Raises:
        Exception: whatever a source raises other than `IngestionError`; nothing is planned then.
    """
    try:
        ingest(sources, connect)
    except IngestionError:
        logger.exception("The pipeline stopped before planning: a raw source could not be loaded, and its raw table keeps its previous rows.")
        return 1
    try:
        transform([source.table_name for source in sources], open_planner)
    except PlanError:
        logger.exception(
            "The pipeline stopped. After a failed audit, the model that failed it keeps the rows that failed and the models downstream of it keep their last good rows; a failed model test applies nothing. The audit or model test that failed is logged above."
        )
        return 1
    return 0


def ingest(sources: Sequence[RawSource], connect: Callable[[], duckdb.DuckDBPyConnection]) -> None:
    """Run every ingestion adapter on one warehouse connection, then release it for SQLMesh."""
    con: duckdb.DuckDBPyConnection = connect()
    source: RawSource
    try:
        for source in sources:
            row_count: int = source.load(con)
            logger.info("%s: %d rows", source.table_name, row_count)
    finally:
        con.close()


def transform(raw_tables: Sequence[str], open_planner: Callable[[], Planner]) -> None:
    """Apply model changes to prod, then restate every model downstream of the freshly ingested raw tables.

    The adapters replace raw tables on every run, so already-computed intervals must be restated; a plain `run()` would treat them as
    done and silently skip the new data. A failing audit raises `PlanError`: downstream models are skipped, but the failing model's own
    table keeps the rows that failed until fixed.
    """
    planner: Planner = open_planner()
    try:
        planner.plan(auto_apply=True, no_prompts=True)
        planner.plan(restate_models=list(raw_tables), auto_apply=True, no_prompts=True)
    finally:
        planner.close()
