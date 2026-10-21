### ADR-0004: `CsvSource` takes its table name and column types from `external_models.yaml`
- Status: Accepted
- Date: 2026-10-17
- Context: A raw table's schema must match its declaration in `external_models.yaml`. The design-spec draft passed `column_types` to the adapter by hand, which duplicates the YAML and can drift from it, and it interpolated the table name into DDL without checking it.
- Decision: `CsvSource(table_name, path, contract)` reads the contract on `load()`. It rejects a name that is not declared there or is not `<schema>.<table>` made of plain identifiers (`[A-Za-z_][A-Za-z0-9_]*`), takes the column types from the declaration, and only then builds DDL.
- Alternatives: keep `column_types` as a parameter and check only that `table_name` is declared, rejected because the types could drift from the contract and the schema identifier would go unchecked.
- Consequences: One source of truth for names and types, and the identifiers interpolated into DDL are checked. The constructor differs from the draft's (`contract` instead of `column_types`).
