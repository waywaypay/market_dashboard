"""No-look-ahead gate: no stage may read or emit data timestamped after
`generated_at`. A pre-market brief that quotes a story from after it was
generated is worse than useless — it's a credibility hole.
"""

from __future__ import annotations

import pytest

from pipeline.contracts.universe import load_universe
from pipeline.evals.harness import EVAL_NOW, UNIVERSES_DIR
from pipeline.orchestrator import run_universe
from pipeline.providers.registry import build_providers

UNIVERSES = ["diagnostics", "fintech"]
# Items deliberately seeded with a future timestamp in the fixtures.
FUTURE_ITEMS = {"diagnostics": "dx-016", "fintech": "ft-014"}


@pytest.mark.parametrize("universe_id", UNIVERSES)
def test_no_item_after_generated_at(universe_id: str, tmp_path) -> None:
    universe = load_universe(UNIVERSES_DIR / f"{universe_id}.yaml")
    brief = run_universe(
        universe,
        now=EVAL_NOW,
        web_public=tmp_path,
        default=False,
        providers=build_providers(universe, EVAL_NOW),
        send_email=False,
    )

    all_items = [
        i for rows in brief.by_company.values() for i in rows
    ] + brief.sector_headlines + brief.priority_signals
    for item in all_items:
        assert item.ts <= brief.generated_at, (
            f"{item.id} ts {item.ts.isoformat()} is after generated_at "
            f"{brief.generated_at.isoformat()}"
        )

    # source health timestamps must also respect the clock
    for health in brief.source_status:
        if health.last_ts is not None:
            assert health.last_ts <= brief.generated_at


@pytest.mark.parametrize("universe_id", UNIVERSES)
def test_seeded_future_item_is_dropped(universe_id: str, tmp_path) -> None:
    """The future-dated fixture item must never appear anywhere in the brief —
    proving the guard actually fires (not just that nothing happened to be late)."""
    universe = load_universe(UNIVERSES_DIR / f"{universe_id}.yaml")
    brief = run_universe(
        universe,
        now=EVAL_NOW,
        web_public=tmp_path,
        default=False,
        providers=build_providers(universe, EVAL_NOW),
        send_email=False,
    )
    future_id = FUTURE_ITEMS[universe_id]
    present = {i.id for rows in brief.by_company.values() for i in rows}
    present |= {i.id for i in brief.sector_headlines}
    present |= {i.id for i in brief.priority_signals}
    assert future_id not in present, f"{future_id} leaked past the look-ahead guard"
