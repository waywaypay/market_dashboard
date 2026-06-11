"""Deterministic fuse-stage gate: the right driver is attributed to a known
unusual move, and unusual-move detection matches the configured thresholds."""

from __future__ import annotations

import pytest

from pipeline.evals.harness import (
    EVAL_NOW,
    load_eval_universe,
)
from pipeline.providers.registry import build_classifier, build_providers
from pipeline.stages.fuse import run_fuse
from pipeline.stages.process import run_process
from pipeline.stages.source import run_source

# (universe, flagged ticker, expected driver item id, expected flag reason)
ATTRIBUTION_CASES = [
    ("diagnostics", "NTRA", "dx-001", "sigma+rvol"),
    ("diagnostics", "GH", "dx-004", "sigma"),    # materiality-5 rate cut beats m3 dx-018
    ("diagnostics", "WGS", "dx-005", "rvol"),
    ("fintech", "AFRM", "ft-001", "sigma+rvol"),  # Walmart deal beats m3 ABS ft-016
    ("fintech", "UPST", "ft-003", "sigma"),
    ("fintech", "FOUR", "ft-012", "rvol"),
]


def _fuse(universe_id: str):
    """Drive source -> process -> fuse so rvol is derived exactly as in prod
    (the source stage owns rvol; calling snapshot() directly would skip it)."""
    universe = load_eval_universe(universe_id)
    providers = build_providers(universe_id, EVAL_NOW)
    src = run_source(universe, providers, EVAL_NOW)
    proc = run_process(src.items, universe, build_classifier(universe_id))
    return universe, run_fuse(proc.items, src.quotes, universe)


@pytest.mark.parametrize("universe_id,ticker,driver_id,reason", ATTRIBUTION_CASES)
def test_driver_attribution(universe_id: str, ticker: str, driver_id: str, reason: str) -> None:
    _, fused = _fuse(universe_id)
    quote = next(q for q in fused.quotes if q.ticker == ticker)

    assert quote.flagged, f"{ticker} should be flagged as an unusual move"
    assert quote.flag_reason == reason, f"{ticker} reason {quote.flag_reason!r} != {reason!r}"
    assert quote.driver_item_id == driver_id, (
        f"{ticker} driver {quote.driver_item_id!r} != expected {driver_id!r}"
    )

    # the attributed item carries the badge back, and is marked the driver
    driver = next(i for i in fused.items if i.id == driver_id)
    assert driver.is_driver
    assert driver.price_reaction is not None
    assert driver.price_reaction.ticker == ticker
    assert driver.price_reaction.flagged
    assert driver in fused.priority_signals


def test_driver_is_highest_materiality_same_ticker() -> None:
    """GH has two same-ticker items (dx-004 m5 regulatory, dx-018 m3 clinical);
    the higher-materiality one must win — the core attribution rule."""
    _, fused = _fuse("diagnostics")
    gh = next(q for q in fused.quotes if q.ticker == "GH")
    gh_items = sorted(
        (i for i in fused.items if i.ticker == "GH"), key=lambda i: -i.materiality
    )
    assert len(gh_items) >= 2, "expected multiple GH items to make this test meaningful"
    assert gh.driver_item_id == gh_items[0].id


def test_calm_tickers_not_flagged() -> None:
    """A move below both thresholds must not be flagged or attributed."""
    _, fused = _fuse("diagnostics")
    vcyt = next(q for q in fused.quotes if q.ticker == "VCYT")  # +1.2%, rvol<2
    assert not vcyt.flagged
    assert vcyt.driver_item_id is None


@pytest.mark.parametrize("universe_id", ["diagnostics", "fintech"])
def test_flag_thresholds_match_config(universe_id: str) -> None:
    """Every flagged quote must actually breach a configured threshold, and no
    un-flagged quote should breach one (deterministic, no false flags)."""
    universe, fused = _fuse(universe_id)
    t = universe.thresholds
    for q in fused.quotes:
        breaches = (q.sigma > 0 and abs(q.chg_pct) >= t.sigma_multiple * q.sigma) or (
            q.rvol is not None and q.rvol >= t.rvol
        )
        assert q.flagged == breaches, f"{q.ticker}: flagged={q.flagged} but breaches={breaches}"
