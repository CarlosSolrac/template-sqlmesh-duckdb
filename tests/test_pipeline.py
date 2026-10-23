"""End-to-end runs of the composition root against a copy of the SQLMesh project in a temporary directory."""

import logging
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from project.main import main

ROOT: Path = Path(__file__).resolve().parents[1]
SAMPLE_CSV: Path = ROOT / "tests" / "fixtures" / "orders.csv"
MART_QUERY: str = "SELECT order_date, order_count, revenue FROM orders.fct_daily_revenue ORDER BY order_date"
LF: str = chr(10)
REVENUE_AUDIT: Path = ROOT / "tests" / "fixtures" / "assert_revenue_below_100.sql"
SAMPLE_MART: list[tuple[date, int, Decimal]] = [(date(2026, 10, 1), 2, Decimal("20.00")), (date(2026, 10, 2), 1, Decimal("5.00"))]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the SQLMesh project with the sample CSV as its input and no warehouse yet; the working directory is outside it."""
    copy: Path = tmp_path / "project"
    copy.mkdir()
    name: str
    for name in ("config.yaml", "external_models.yaml"):
        shutil.copy2(ROOT / name, copy / name)
    for name in ("models", "audits"):
        shutil.copytree(ROOT / name, copy / name)
    (copy / "tests").mkdir()
    shutil.copy2(ROOT / "tests" / "test_models.yaml", copy / "tests" / "test_models.yaml")
    (copy / "data").mkdir()
    shutil.copy2(SAMPLE_CSV, copy / "data" / "orders.csv")
    monkeypatch.chdir(tmp_path)
    return copy


def add_orders(project: Path, rows: str) -> None:
    """Replace the project's input with the sample orders plus `rows`, keeping the sample's LF line endings."""
    (project / "data" / "orders.csv").write_text(SAMPLE_CSV.read_text(encoding="utf-8") + rows, encoding="utf-8", newline="\n")


def query(database: Path, sql: str) -> list[tuple[object, ...]]:
    """Run a read-only query on a DuckDB file."""
    con: duckdb.DuckDBPyConnection
    with duckdb.connect(database, read_only=True) as con:
        return con.execute(sql).fetchall()


def failed_copy(project: Path) -> Path:
    """Return the newest kept copy of a failed run."""
    return max((project / "data").glob("warehouse.failed-*.duckdb"))


def mart_rows(project: Path) -> list[tuple[date, int, Decimal]]:
    """Read the daily revenue mart from the project's warehouse."""
    con: duckdb.DuckDBPyConnection
    with duckdb.connect(project / "data" / "warehouse.duckdb", read_only=True) as con:
        return con.execute(MART_QUERY).fetchall()


def test_a_full_run_from_an_empty_warehouse_exits_0_and_fills_the_mart(project: Path, caplog: pytest.LogCaptureFixture) -> None:
    status: int = main(project)

    assert status == 0
    assert mart_rows(project) == SAMPLE_MART
    assert "raw.orders: 4 rows" in caplog.messages
    assert logging.getLogger().getEffectiveLevel() == logging.WARNING


def test_a_negative_amount_exits_1_and_the_mart_keeps_its_last_good_rows(project: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert main(project) == 0
    add_orders(project, "4,9,-3.00,2026-10-02 12:00:00+00\n")

    status: int = main(project)

    assert status == 1
    assert mart_rows(project) == SAMPLE_MART
    assert "orders.stg_orders" in caplog.text
    assert "'assert_positive_amount' audit error: 1 row failed" in caplog.text
    assert "The live warehouse is unchanged" in caplog.text
    assert "warehouse.failed-*.duckdb" in caplog.text
    assert query(project / "data" / "warehouse.duckdb", "SELECT count(*) FROM orders.stg_orders WHERE amount <= 0") == [(0,)]
    assert query(failed_copy(project), "SELECT count(*) FROM orders.stg_orders WHERE amount <= 0") == [(1,)]


def test_a_failing_model_test_exits_1_and_the_log_names_the_test(project: Path, caplog: pytest.LogCaptureFixture) -> None:
    tests_file: Path = project / "tests" / "test_models.yaml"
    tests_file.write_text(tests_file.read_text(encoding="utf-8").replace("revenue: 20.00", "revenue: 21.00"), encoding="utf-8", newline="\n")

    status: int = main(project)

    assert status == 1
    assert "test_fct_daily_revenue_sums_by_day" in caplog.text


def test_a_day_whose_only_order_has_no_amount_exits_1_and_the_mart_keeps_its_last_good_rows(project: Path) -> None:
    assert main(project) == 0
    add_orders(project, "4,9,,2026-10-03 12:00:00+00\n")

    status: int = main(project)

    assert status == 1
    assert mart_rows(project) == SAMPLE_MART


def test_two_different_versions_of_an_order_with_the_same_timestamp_exit_1_and_the_mart_keeps_its_last_good_rows(project: Path) -> None:
    assert main(project) == 0
    add_orders(project, "1,7,99.00,2026-10-01 11:00:00+00\n")

    status: int = main(project)

    assert status == 1
    assert mart_rows(project) == SAMPLE_MART


def test_a_csv_with_an_undeclared_column_exits_1_and_the_mart_keeps_its_last_good_rows(project: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert main(project) == 0
    (project / "data" / "orders.csv").write_text("order_id,customer_id,amount,ordered_at,coupon\n9,9,1.00,2026-10-03 09:00:00+00,X\n", encoding="utf-8", newline="\n")

    status: int = main(project)

    assert status == 1
    assert mart_rows(project) == SAMPLE_MART
    assert "coupon" in caplog.text


def test_a_mart_audit_that_fails_leaves_the_warehouse_at_its_last_good_rows(project: Path) -> None:
    shutil.copy2(REVENUE_AUDIT, project / "audits" / REVENUE_AUDIT.name)
    mart: Path = project / "models" / "marts" / "fct_daily_revenue.sql"
    old: str = "unique_values(columns := (order_date))"
    mart.write_text(mart.read_text(encoding="utf-8").replace(old, old + "," + LF + "    assert_revenue_below_100"), encoding="utf-8", newline=LF)
    assert main(project) == 0
    add_orders(project, "4,9,500.00,2026-10-02 12:00:00+00" + LF)

    status: int = main(project)

    assert status == 1
    assert mart_rows(project) == SAMPLE_MART
    assert query(failed_copy(project), MART_QUERY)[-1][2] == Decimal("505.00")


def test_a_successful_run_after_a_failed_one_keeps_the_failed_copy_and_removes_the_build_directory(project: Path) -> None:
    assert main(project) == 0
    add_orders(project, "4,9,-3.00,2026-10-02 12:00:00+00" + LF)
    assert main(project) == 1
    add_orders(project, "")

    status: int = main(project)

    assert status == 0
    assert len(list((project / "data").glob("warehouse.failed-*.duckdb"))) == 1
    assert not (project / "data" / ".build").exists()


def test_a_same_day_rerun_picks_up_new_rows(project: Path) -> None:
    assert main(project) == 0
    add_orders(project, "4,9,3.00,2026-10-02 12:00:00+00\n")

    status: int = main(project)

    assert status == 0
    assert mart_rows(project) == [(date(2026, 10, 1), 2, Decimal("20.00")), (date(2026, 10, 2), 2, Decimal("8.00"))]


def test_a_project_dir_relative_to_the_working_directory_runs_that_project(project: Path) -> None:
    status: int = main(Path(project.name))

    assert status == 0
    assert mart_rows(project) == SAMPLE_MART
