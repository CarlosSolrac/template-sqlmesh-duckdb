### ADR-0005: Reject a CSV that has columns the contract does not declare
- Status: Accepted
- Date: 2026-10-17
- Context: A CSV with an extra column loaded into `raw.orders` with that column, though `RawSource` says the table's schema must match `external_models.yaml`. A CSV missing a declared column already fails in DuckDB.
- Decision: `CsvSource.load` compares the CSV's columns with the declaration before any DDL and raises `IngestionError` when there are extras. The existing table is kept.
- Alternatives: load only the declared columns and drop the rest silently, rejected because a renamed column would then surface only as a missing-column error; load everything as the draft did, rejected because the raw table could drift from its declared schema.
- Consequences: The raw table always matches the contract, and a changed upstream file fails loudly at the boundary. An upstream file that adds a column stops ingestion until `external_models.yaml` is updated.
