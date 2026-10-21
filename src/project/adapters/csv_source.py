"""CSV ingestion adapter implementing the RawSource port."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeIs

import duckdb
from ruamel.yaml import YAML

from .errors import IngestionError

# Identifiers cannot be bound as parameters, so the DDL below interpolates them; only `<schema>.<table>` names made of
# plain identifiers, and declared in the contract, ever reach it.
QUALIFIED_NAME: re.Pattern[str] = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True, slots=True)
class CsvSource:
    """Loads a CSV file into a raw table, typed by that table's declaration in the contract (`external_models.yaml`).

    `table_name` must be declared in `contract` as `<schema>.<table>`; anything else is rejected before any DDL is built.
    """

    table_name: str
    path: Path
    contract: Path

    def load(self, con: duckdb.DuckDBPyConnection) -> int:
        """Replace `table_name` with the CSV contents and return the loaded row count.

        The replacement is one transaction, so a load that fails (missing file, a value of the wrong type) keeps the previous table.

        Raises:
            IngestionError: `table_name` is not declared in the contract or is not a `<schema>.<table>` name of plain identifiers, the
                CSV has columns the contract does not declare, or the CSV cannot be read, lacks a declared column, or holds a value of
                the wrong type.
        """
        try:
            return self.replace(con)
        except (ValueError, duckdb.Error) as error:
            message: str = f"{self.table_name} was not loaded from {self.path}: {error}"
            raise IngestionError(message) from error

    def replace(self, con: duckdb.DuckDBPyConnection) -> int:
        """Replace the table in one transaction and return the row count; `load` turns what this raises into an `IngestionError`."""
        column_types: dict[str, str] = declared_columns(self.contract, self.table_name)
        schema: str = schema_of(self.table_name)
        relation: duckdb.DuckDBPyRelation = con.read_csv(self.path, dtype=column_types)
        undeclared: list[str] = [column for column in relation.columns if column not in column_types]
        if undeclared:
            message: str = f"{self.path} has columns {undeclared} that {self.table_name!r} does not declare in {self.contract}"
            raise ValueError(message)
        con.begin()
        try:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            con.execute(f"DROP TABLE IF EXISTS {self.table_name}")
            relation.create(self.table_name)
        except duckdb.Error:
            con.rollback()
            raise
        con.commit()
        return con.table(self.table_name).shape[0]


class YamlLoader(Protocol):
    """The part of ruamel.yaml's `YAML` this module uses, typed (ruamel's own `load` signature is partially unknown to pyright)."""

    def load(self, stream: Path) -> object:
        """Parse the YAML document at `stream`."""


def safe_loader() -> YamlLoader:
    """Return a ruamel.yaml loader that builds only plain Python objects."""
    return YAML(typ="safe")


def declared_columns(contract: Path, table_name: str) -> dict[str, str]:
    """Return the column types the contract declares for `table_name`.

    Raises:
        ValueError: the contract does not declare `table_name` with a mapping of columns (including a contract that is not a list of
            declarations).
    """
    entries: object = safe_loader().load(contract)
    entry: object
    for entry in entries if is_list(entries) else []:
        if is_mapping(entry) and entry.get("name") == table_name:
            columns: object = entry.get("columns")
            if is_mapping(columns):
                return {str(name): str(column_type) for name, column_type in columns.items()}
    message: str = f"{table_name!r} is not declared in {contract} with its columns; only declared raw tables may be loaded"
    raise ValueError(message)


def schema_of(table_name: str) -> str:
    """Return the schema part of a `<schema>.<table>` name of plain identifiers.

    Raises:
        ValueError: `table_name` has another shape.
    """
    qualified: re.Match[str] | None = QUALIFIED_NAME.fullmatch(table_name)
    if qualified is None:
        message: str = f"{table_name!r} is not a <schema>.<table> name of plain identifiers"
        raise ValueError(message)
    return qualified.group(1)


def is_list(value: object) -> TypeIs[list[object]]:
    """Return whether a parsed YAML value is a sequence."""
    return isinstance(value, list)


def is_mapping(value: object) -> TypeIs[dict[object, object]]:
    """Return whether a parsed YAML value is a mapping."""
    return isinstance(value, dict)
