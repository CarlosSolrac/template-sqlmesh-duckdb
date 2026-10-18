### ADR-0001: Use SQLMesh for transformations, with an exit path
- Status: Accepted
- Date: 2026-10-17
- Context: SQL stages need dependency ordering, blocking invariant checks, unit tests, lineage, and incremental loads. SQLMesh provides these natively on DuckDB but is pre-1.0, community-governed under the Linux Foundation, and has a smaller ecosystem than dbt.
- Decision: Use SQLMesh, pinned to an exact version: `sqlmesh==0.236.2` in `pyproject.toml`. Models are plain DuckDB SQL; no Jinja; no SQLMesh macros except `@this_model` in audits and the incremental time-range macros; any other macro needs its own ADR. That is the exit path: below each `MODEL (...)` header the SQL is plain DuckDB SQL, so moving to another runner means rewriting the headers and audits, not untangling templates.
- Alternatives: dbt: rejected because of its Jinja templating and weaker Python models, despite its larger ecosystem; a hand-written stage runner: rejected because it has no lineage, incremental loads, or test harness.
- Consequences: Lineage, plans, unit tests, and audits come free. SQLMesh is pre-1.0, so a release can break the project: review release notes for breaking changes before raising the pin. The pin, 0.236.2, was the newest release at least two weeks old on 2026-10-17, and this template's own tests run on it.
