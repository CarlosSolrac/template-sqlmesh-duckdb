"""Adapter tests: each adapter runs against an in-memory DuckDB and must honor its declared schema."""

from pathlib import Path

import duckdb
import pytest

from project.adapters.csv_source import CsvSource, IngestionError
from project.ports import RawSource

ROOT: Path = Path(__file__).resolve().parents[1]
CONTRACT: Path = ROOT / "external_models.yaml"
SAMPLE_CSV: Path = ROOT / "tests" / "fixtures" / "orders.csv"
SCHEMAS_QUERY: str = "SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ('main', 'information_schema', 'pg_catalog')"


def test_csv_source_loads_rows_with_the_declared_types() -> None:
    con: duckdb.DuckDBPyConnection = duckdb.connect(":memory:")
    source: RawSource = CsvSource(table_name="raw.orders", path=SAMPLE_CSV, contract=CONTRACT)

    row_count: int = source.load(con)

    schema_rows: list[tuple[str, str]] = con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'raw' AND table_name = 'orders' ORDER BY ordinal_position").fetchall()
    assert row_count == 4
    assert dict(schema_rows) == {"order_id": "INTEGER", "customer_id": "INTEGER", "amount": "DECIMAL(12,2)", "ordered_at": "TIMESTAMP WITH TIME ZONE"}


def test_csv_source_replaces_the_existing_table(tmp_path: Path) -> None:
    csv_path: Path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,customer_id,amount,ordered_at\n1,7,10.00,2026-10-01 10:00:00+00\n", encoding="utf-8")
    con: duckdb.DuckDBPyConnection = duckdb.connect(":memory:")
    CsvSource(table_name="raw.orders", path=SAMPLE_CSV, contract=CONTRACT).load(con)

    row_count: int = CsvSource(table_name="raw.orders", path=csv_path, contract=CONTRACT).load(con)

    assert row_count == 1
    assert con.table("raw.orders").shape[0] == 1


@pytest.mark.parametrize(
    "csv_text",
    [None, "order_id,customer_id,amount,ordered_at\nnot-a-number,7,10.00,2026-10-01 10:00:00+00\n"],
    ids=["missing-file", "value-of-the-wrong-type"],
)
def test_csv_source_keeps_the_existing_table_when_a_reload_fails(csv_text: str | None, tmp_path: Path) -> None:
    csv_path: Path = tmp_path / "orders.csv"
    if csv_text is not None:
        csv_path.write_text(csv_text, encoding="utf-8")
    con: duckdb.DuckDBPyConnection = duckdb.connect(":memory:")
    CsvSource(table_name="raw.orders", path=SAMPLE_CSV, contract=CONTRACT).load(con)

    with pytest.raises(IngestionError):
        CsvSource(table_name="raw.orders", path=csv_path, contract=CONTRACT).load(con)

    assert con.table("raw.orders").shape[0] == 4


def test_csv_source_rejects_columns_the_contract_does_not_declare_before_any_ddl(tmp_path: Path) -> None:
    csv_path: Path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,customer_id,amount,ordered_at,coupon\n1,7,10.00,2026-10-01 10:00:00+00,FALL10\n", encoding="utf-8")
    con: duckdb.DuckDBPyConnection = duckdb.connect(":memory:")

    with pytest.raises(IngestionError, match=r"has columns \['coupon'\] that 'raw\.orders' does not declare"):
        CsvSource(table_name="raw.orders", path=csv_path, contract=CONTRACT).load(con)

    assert con.execute(SCHEMAS_QUERY).fetchall() == []


@pytest.mark.parametrize(
    "table_name",
    ["raw.customers", "staging.orders", "raw.orders; DROP TABLE raw.orders", "orders"],
    ids=["undeclared-table", "undeclared-schema", "injection", "unqualified"],
)
def test_csv_source_rejects_a_table_the_contract_does_not_declare_before_any_ddl(table_name: str) -> None:
    con: duckdb.DuckDBPyConnection = duckdb.connect(":memory:")

    with pytest.raises(IngestionError, match="is not declared in"):
        CsvSource(table_name=table_name, path=SAMPLE_CSV, contract=CONTRACT).load(con)

    assert con.execute(SCHEMAS_QUERY).fetchall() == []


@pytest.mark.parametrize(
    "declared_name",
    ["orders", "raw.orders;--", "memory.raw.orders", 'raw."orders"'],
    ids=["unqualified", "injection", "three-part", "quoted"],
)
def test_csv_source_rejects_a_declared_name_that_is_not_schema_dot_table_before_any_ddl(declared_name: str, tmp_path: Path) -> None:
    contract: Path = tmp_path / "external_models.yaml"
    contract.write_text(f"- name: '{declared_name}'\n  columns:\n    order_id: INTEGER\n", encoding="utf-8")
    con: duckdb.DuckDBPyConnection = duckdb.connect(":memory:")

    with pytest.raises(IngestionError, match=r"is not a <schema>\.<table> name"):
        CsvSource(table_name=declared_name, path=SAMPLE_CSV, contract=contract).load(con)

    assert con.execute(SCHEMAS_QUERY).fetchall() == []


@pytest.mark.parametrize(
    "contract_text",
    ["name: raw.orders\n", "- raw.orders\n", "- name: raw.orders\n  columns: [order_id]\n"],
    ids=["not-a-list", "entry-not-a-mapping", "columns-not-a-mapping"],
)
def test_csv_source_rejects_a_malformed_contract_before_any_ddl(contract_text: str, tmp_path: Path) -> None:
    contract: Path = tmp_path / "external_models.yaml"
    contract.write_text(contract_text, encoding="utf-8")
    con: duckdb.DuckDBPyConnection = duckdb.connect(":memory:")

    with pytest.raises(IngestionError, match="is not declared in"):
        CsvSource(table_name="raw.orders", path=SAMPLE_CSV, contract=contract).load(con)

    assert con.execute(SCHEMAS_QUERY).fetchall() == []
