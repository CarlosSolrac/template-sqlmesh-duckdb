"""Composition-root unit tests: `run()` with fake sources, an in-memory connection, and a fake planner."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

import duckdb
import pytest
from sqlmesh.utils.errors import PlanError

from project.main import run
from project.ports import Planner, RawSource


@dataclass
class Journal:
    """What the fakes saw, in order."""

    events: list[str] = field(default_factory=list[str])
    connections: list[duckdb.DuckDBPyConnection] = field(default_factory=list[duckdb.DuckDBPyConnection])


@dataclass
class FakeSource:
    """Records each load and reports a fixed row count, or fails."""

    table_name: str
    rows: int
    journal: Journal
    error: Exception | None = None

    def load(self, con: duckdb.DuckDBPyConnection) -> int:
        """Record the load on the journal; raise `error` when one is set."""
        self.journal.events.append(f"load {self.table_name}")
        con.execute("SELECT 1")
        if self.error is not None:
            raise self.error
        return self.rows


@dataclass
class FakePlanner:
    """Records each plan call; the restatement fails when `error` is set."""

    journal: Journal
    error: Exception | None = None

    def plan(self, *, restate_models: Iterable[str] | None = None, no_prompts: bool | None = None, auto_apply: bool | None = None) -> object:
        """Record the call; raise `error` on a restatement when one is set."""
        restated: list[str] | None = None if restate_models is None else list(restate_models)
        self.journal.events.append(f"plan restate={restated} no_prompts={no_prompts} auto_apply={auto_apply}")
        if restated is not None and self.error is not None:
            raise self.error
        return None

    def close(self) -> None:
        """Record the close."""
        self.journal.events.append("close planner")


def connect_in_memory(journal: Journal) -> duckdb.DuckDBPyConnection:
    """Open an in-memory connection and remember it, so a test can check it was closed."""
    journal.events.append("connect")
    con: duckdb.DuckDBPyConnection = duckdb.connect(":memory:")
    journal.connections.append(con)
    return con


def open_after_ingestion_closed(journal: Journal, planner: FakePlanner) -> Planner:
    """Return the planner, first checking that the ingestion connection is already closed (SQLMesh needs the file to itself)."""
    with pytest.raises(duckdb.ConnectionException):
        journal.connections[0].execute("SELECT 1")
    journal.events.append("open planner")
    return planner


def test_run_ingests_then_applies_the_plan_then_restates_the_raw_tables(caplog: pytest.LogCaptureFixture) -> None:
    journal: Journal = Journal()
    sources: list[RawSource] = [FakeSource("raw.orders", 4, journal), FakeSource("raw.customers", 2, journal)]
    planner: FakePlanner = FakePlanner(journal)
    caplog.set_level(logging.INFO)

    status: int = run(sources, lambda: connect_in_memory(journal), lambda: open_after_ingestion_closed(journal, planner))

    assert status == 0
    assert journal.events == [
        "connect",
        "load raw.orders",
        "load raw.customers",
        "open planner",
        "plan restate=None no_prompts=True auto_apply=True",
        "plan restate=['raw.orders', 'raw.customers'] no_prompts=True auto_apply=True",
        "close planner",
    ]
    assert "raw.orders: 4 rows" in caplog.messages
    assert "raw.customers: 2 rows" in caplog.messages


def test_run_returns_1_and_closes_the_planner_when_the_plan_fails(caplog: pytest.LogCaptureFixture) -> None:
    journal: Journal = Journal()
    planner: FakePlanner = FakePlanner(journal, error=PlanError("Plan application failed."))

    status: int = run([FakeSource("raw.orders", 4, journal)], lambda: connect_in_memory(journal), lambda: open_after_ingestion_closed(journal, planner))

    assert status == 1
    assert journal.events[-1] == "close planner"
    assert any(record.levelno == logging.ERROR and record.exc_info is not None and isinstance(record.exc_info[1], PlanError) for record in caplog.records)


def test_run_closes_the_connection_and_opens_no_planner_when_a_source_fails() -> None:
    journal: Journal = Journal()
    planner: FakePlanner = FakePlanner(journal)

    with pytest.raises(ValueError, match="undeclared"):
        run([FakeSource("raw.orders", 4, journal, error=ValueError("undeclared"))], lambda: connect_in_memory(journal), lambda: open_after_ingestion_closed(journal, planner))

    assert journal.events == ["connect", "load raw.orders"]
    with pytest.raises(duckdb.ConnectionException):
        journal.connections[0].execute("SELECT 1")
