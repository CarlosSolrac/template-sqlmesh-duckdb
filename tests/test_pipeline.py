"""End-to-end runs of the composition root against a copy of the SQLMesh project in a temporary directory."""

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


def mart_rows(project: Path) -> list[tuple[date, int, Decimal]]:
    """Read the daily revenue mart from the project's warehouse."""
    con: duckdb.DuckDBPyConnection
    with duckdb.connect(project / "data" / "warehouse.duckdb", read_only=True) as con:
        return con.execute(MART_QUERY).fetchall()


def test_a_full_run_from_an_empty_warehouse_exits_0_and_fills_the_mart(project: Path) -> None:
    status: int = main(project)

    assert status == 0
    assert mart_rows(project) == SAMPLE_MART


def test_a_negative_amount_exits_1_and_the_mart_keeps_its_last_good_rows(project: Path) -> None:
    assert main(project) == 0
    add_orders(project, "4,9,-3.00,2026-10-02 12:00:00+00\n")

    status: int = main(project)

    assert status == 1
    assert mart_rows(project) == SAMPLE_MART


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
