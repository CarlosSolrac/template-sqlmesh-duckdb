### ADR-0002: Replace raw tables and restate downstream on every run
- Status: Accepted
- Date: 2026-10-17
- Context: SQLMesh treats each day's interval as done after the first run, so a plain `run()` (even with `ignore_cron=True`) silently skips new raw data on same-day reruns.
- Decision: Adapters replace raw tables; the composition root applies model changes with `plan()`, then restates every model downstream of the ingested raw tables (`plan(restate_models=<raw tables>)`).
- Alternatives: append-only raw tables with `INCREMENTAL_BY_TIME_RANGE` models and `run()`: rejected for now because it is more complex, although cheaper at scale.
- Consequences: Same-day reruns pick up new raw rows. Every run recomputes all downstream models. Revisit when the numbers of design-spec's §2 (row volumes, growth rate, refresh frequency) call for incremental loads.
