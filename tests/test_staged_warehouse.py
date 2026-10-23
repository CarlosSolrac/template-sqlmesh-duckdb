"""StagedWarehouse against real files in a temporary directory."""

from datetime import UTC, datetime
from pathlib import Path

import duckdb

from project.adapters.staged_warehouse import StagedWarehouse

START: datetime = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)


def make_warehouse(path: Path, value: int) -> None:
    """Create a DuckDB file with one table holding `value`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con: duckdb.DuckDBPyConnection
    with duckdb.connect(path) as con:
        con.execute("CREATE OR REPLACE TABLE t AS SELECT ? AS v", [value])


def value_in(path: Path) -> int:
    """Read the value `make_warehouse` stored."""
    con: duckdb.DuckDBPyConnection
    with duckdb.connect(path, read_only=True) as con:
        rows: list[tuple[int]] = con.execute("SELECT v FROM t").fetchall()
    return rows[0][0]


def test_prepare_returns_a_build_directory_holding_a_copy_of_the_live_warehouse(tmp_path: Path) -> None:
    staged: StagedWarehouse = StagedWarehouse(tmp_path)
    make_warehouse(staged.live, 1)

    build_root: Path = staged.prepare()

    assert build_root == staged.build_root
    assert value_in(staged.build) == 1
    assert value_in(staged.live) == 1


def test_prepare_without_a_live_warehouse_leaves_an_empty_data_directory(tmp_path: Path) -> None:
    staged: StagedWarehouse = StagedWarehouse(tmp_path)

    staged.prepare()

    assert staged.build.parent.is_dir()
    assert not staged.build.exists()


def test_prepare_copies_a_write_ahead_log_next_to_the_live_warehouse(tmp_path: Path) -> None:
    staged: StagedWarehouse = StagedWarehouse(tmp_path)
    make_warehouse(staged.live, 1)
    staged.live.with_name(staged.live.name + ".wal").write_bytes(b"log")

    staged.prepare()

    assert staged.build.with_name(staged.build.name + ".wal").read_bytes() == b"log"


def test_prepare_discards_what_an_interrupted_run_left_in_the_build_directory(tmp_path: Path) -> None:
    staged: StagedWarehouse = StagedWarehouse(tmp_path)
    make_warehouse(staged.build, 99)

    staged.prepare()

    assert not staged.build.exists()


def test_commit_replaces_the_live_warehouse_and_the_build_directory_and_keeps_earlier_failed_copies(tmp_path: Path) -> None:
    staged: StagedWarehouse = StagedWarehouse(tmp_path, clock=lambda: START)
    make_warehouse(staged.live, 1)
    staged.prepare()
    make_warehouse(staged.build, 5)
    staged.discard()
    staged.prepare()
    make_warehouse(staged.build, 2)
    staged.live.with_name(staged.live.name + ".wal").write_bytes(b"stale")

    staged.commit()

    assert value_in(staged.live) == 2
    assert [value_in(path) for path in staged.failed_copies()] == [5]
    assert not staged.live.with_name(staged.live.name + ".wal").exists()
    assert not staged.build_root.exists()


def test_commit_creates_the_live_warehouse_on_the_first_run(tmp_path: Path) -> None:
    staged: StagedWarehouse = StagedWarehouse(tmp_path)
    staged.prepare()
    make_warehouse(staged.build, 2)

    staged.commit()

    assert value_in(staged.live) == 2


def test_discard_keeps_the_live_warehouse_and_the_build_as_a_timestamped_failed_copy(tmp_path: Path) -> None:
    staged: StagedWarehouse = StagedWarehouse(tmp_path, clock=lambda: START)
    make_warehouse(staged.live, 1)
    staged.prepare()
    make_warehouse(staged.build, 3)

    staged.discard()

    assert value_in(staged.live) == 1
    assert [path.name for path in staged.failed_copies()] == ["warehouse.failed-20261022T120000000000.duckdb"]
    assert value_in(staged.failed_copies()[0]) == 3
    assert not staged.build_root.exists()


def test_discard_keeps_only_the_newest_failed_copies(tmp_path: Path) -> None:
    ticks: list[datetime] = [START.replace(minute=minute) for minute in range(4)]
    staged: StagedWarehouse = StagedWarehouse(tmp_path, keep=2, clock=lambda: ticks.pop(0))
    make_warehouse(staged.live, 1)
    value: int
    for value in (10, 11, 12):
        staged.prepare()
        make_warehouse(staged.build, value)
        staged.discard()

    assert [value_in(path) for path in staged.failed_copies()] == [11, 12]


def test_discard_without_a_build_keeps_the_live_warehouse_and_adds_no_failed_copy(tmp_path: Path) -> None:
    staged: StagedWarehouse = StagedWarehouse(tmp_path, clock=lambda: START)
    make_warehouse(staged.live, 1)
    staged.prepare()
    staged.build.unlink()

    staged.discard()

    assert value_in(staged.live) == 1
    assert staged.failed_copies() == []
    assert not staged.build_root.exists()
