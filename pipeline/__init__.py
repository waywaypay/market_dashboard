"""Morning competitive-intelligence pipeline.

Layout:
    contracts/     Pydantic models — the ONLY cross-stage interface
    providers/     vendor-neutral interfaces + FixtureProvider + real stubs
    stages/        source -> process -> fuse -> output, each pure & typed
    orchestrator   deterministic DAG (no LLM routing)
    evals/         gate evals (run via `make eval`)
    fixtures/      synthetic inputs for the two shipped universes
"""
