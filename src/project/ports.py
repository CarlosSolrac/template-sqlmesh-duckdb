"""Ports: interfaces the composition root depends on."""

from collections.abc import Iterable
from typing import Protocol

import duckdb


class IngestionError(Exception):
    """A raw source could not be loaded; the run stops before anything is planned and the warehouse keeps its previous raw table."""


class RawSource(Protocol):
    """Port that lands one raw table in the warehouse; its schema must match `external_models.yaml`."""

    @property
    def table_name(self) -> str:
        """Schema-qualified name of the raw table this source produces (e.g. `raw.orders`)."""
        ...

    def load(self, con: duckdb.DuckDBPyConnection) -> int:
        """Create or replace the raw table on the given connection and return its row count.

        Raises:
            IngestionError: the source cannot be loaded; the previous table is kept.
        """
        ...


class Planner(Protocol):
    """Port that applies model changes and restatements to the warehouse; `sqlmesh.Context` satisfies it as it stands."""

    def plan(self, *, restate_models: Iterable[str] | None = None, no_prompts: bool | None = None, auto_apply: bool | None = None) -> object:
        """Build a plan for the prod environment, restating `restate_models` and everything downstream of them; apply it if `auto_apply`.

        Raises:
            sqlmesh.utils.errors.PlanError: applying the plan failed, for example a blocking audit.
        """
        ...

    def close(self) -> None:
        """Release the warehouse connection, so the file can be opened again."""
        ...
