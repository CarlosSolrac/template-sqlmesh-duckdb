### ADR-0003: Reach SQLMesh through a `Planner` port that `sqlmesh.Context` satisfies
- Status: Accepted
- Date: 2026-10-17
- Context: The design-spec draft built `sqlmesh.Context` inline in `transform()`, so the composition root could not be tested with a fake. The context must also be created only after ingestion closes its DuckDB connection, and closed after use so a second run in the same process (the end-to-end tests) can reopen the file.
- Decision: `ports.py` defines a `Planner` Protocol (`plan(*, restate_models, no_prompts, auto_apply)` and `close()`) that `sqlmesh.Context` satisfies structurally. `run()` takes a `Callable[[], Planner]` factory, and `main()` passes `lambda: Context(paths=...)`.
- Alternatives: a domain-named `Transformer` port with an adapter module wrapping `Context`, rejected because the wrapper would only forward calls; patching `sqlmesh.Context` in tests, rejected because the project's testing rules prefer injection to patching.
- Consequences: Unit tests of `run()` use a fake planner. The port mirrors a subset of a third-party signature, so a SQLMesh rename of those parameters shows up as a type error in pyright and mypy rather than at run time.
