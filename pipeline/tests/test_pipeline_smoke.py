"""End-to-end smoke: the orchestrator produces a contract-valid DailyBrief for
every shipped universe, and config alone drives the taxonomy/subject/voice
(generalization is provable by swapping the YAML)."""

from __future__ import annotations

import json

import pytest

from pipeline.contracts import DailyBrief
from pipeline.contracts.universe import discover_universes, load_universe
from pipeline.evals.harness import EVAL_NOW
from pipeline.orchestrator import run_universe
from pipeline.providers.registry import build_providers

UNIVERSE_IDS = ["diagnostics", "fintech"]


@pytest.mark.parametrize("universe_id", UNIVERSE_IDS)
def test_orchestrator_produces_valid_brief(universe_id: str, tmp_path) -> None:
    from pipeline.evals.harness import UNIVERSES_DIR

    universe = load_universe(UNIVERSES_DIR / f"{universe_id}.yaml")
    brief = run_universe(
        universe,
        now=EVAL_NOW,
        web_public=tmp_path,
        default=True,
        providers=build_providers(universe, EVAL_NOW),
        send_email=False,
    )
    # round-trips through the contract (write -> read -> validate)
    written = json.loads((tmp_path / "briefs" / f"{universe_id}.json").read_text())
    reparsed = DailyBrief.model_validate(written)
    assert reparsed.universe_id == universe_id

    # config drives presentation: subject + taxonomy come from the YAML
    assert brief.subject_ticker == universe.subject.ticker
    assert brief.categories == universe.categories
    assert brief.display_tz == universe.delivery.tz

    # subject pinned first in the market strip
    assert brief.market[0].ticker == universe.subject.ticker

    # every rendered item carries a category from the configured taxonomy
    all_items = [i for rows in brief.by_company.values() for i in rows]
    all_items += brief.sector_headlines
    assert all_items, "brief should not be empty on fixtures"
    for item in all_items:
        assert item.category in universe.categories


def test_two_universes_shipped() -> None:
    """Generalization is only provable if a second sector actually ships."""
    found = {p.stem for p in discover_universes()}
    assert {"diagnostics", "fintech"} <= found


def test_switching_universe_reskins_from_config_alone(tmp_path) -> None:
    """Same code path, different YAML -> different subject, taxonomy, voice."""
    from pipeline.evals.harness import UNIVERSES_DIR

    briefs = {}
    for uid in UNIVERSE_IDS:
        universe = load_universe(UNIVERSES_DIR / f"{uid}.yaml")
        briefs[uid] = run_universe(
            universe, now=EVAL_NOW, web_public=tmp_path, default=False,
            providers=build_providers(universe, EVAL_NOW), send_email=False,
        )
    assert briefs["diagnostics"].categories != briefs["fintech"].categories
    assert briefs["diagnostics"].subject_ticker != briefs["fintech"].subject_ticker
