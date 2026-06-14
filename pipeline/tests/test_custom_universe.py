"""Custom (user-created) universes: spec building, provider selection, and the
manifest `custom` flag. Offline — no network, no fixtures."""

from __future__ import annotations

import json

import pytest

from pipeline.contracts import Counts, DailyBrief
from pipeline.contracts.universe import UniverseConfig
from pipeline.custom_universe import UniverseSpecError, build_spec, slugify, unique_id
from pipeline.providers.fixture import FixtureClassifierProvider, RulesClassifierProvider
from pipeline.providers.registry import build_custom_providers
from pipeline.stages.output import write_artifacts


def test_build_spec_happy_path_validates_as_a_universe():
    spec = build_spec(
        {
            "label": "Mega-cap Tech",
            "subject_ticker": "aapl",  # lowercased input is normalized
            "peer_tickers": ["MSFT", "googl", "AAPL", "", "MSFT"],  # dupes/subject/blank dropped
            "sector_keywords": ["cloud", " ai "],
        },
        existing_ids=set(),
    )
    assert spec["id"] == "user-mega-cap-tech"
    assert spec["custom"] is True
    assert spec["subject"] == {"ticker": "AAPL", "name": "AAPL"}
    assert [p["ticker"] for p in spec["peers"]] == ["MSFT", "GOOGL"]
    assert spec["sector_keywords"] == ["cloud", "ai"]

    u = UniverseConfig.model_validate(spec)  # the real contract accepts it
    assert u.custom is True
    assert u.tickers == ["AAPL", "MSFT", "GOOGL"]


def test_build_spec_rejects_bad_input():
    with pytest.raises(UniverseSpecError):
        build_spec({"label": "", "subject_ticker": "AAPL"}, set())
    with pytest.raises(UniverseSpecError):
        build_spec({"label": "X", "subject_ticker": "not a ticker!"}, set())
    with pytest.raises(UniverseSpecError):
        build_spec(
            {"label": "Too big", "subject_ticker": "A", "peer_tickers": [f"T{i}" for i in range(40)]},
            set(),
        )


def test_unique_id_avoids_collisions():
    assert unique_id(slugify("My Set"), set()) == "user-my-set"
    assert unique_id(slugify("My Set"), {"user-my-set"}) == "user-my-set-2"
    assert unique_id(slugify("My Set"), {"user-my-set", "user-my-set-2"}) == "user-my-set-3"


def test_custom_universe_never_uses_the_fixture_classifier(monkeypatch):
    # No LLM keys -> build_classifier would pick the fixture classifier, which
    # needs per-universe canned files a custom universe lacks. build_custom_providers
    # must swap in the keyless rules classifier instead.
    for var in ("ANTHROPIC_API_KEY", "VENICE_API_KEY", "VENICE_KEY", "BRIEF_CLASSIFIER"):
        monkeypatch.delenv(var, raising=False)
    u = build_spec({"label": "Custom", "subject_ticker": "AAPL"}, set())
    universe = UniverseConfig.model_validate(u)

    providers = build_custom_providers(universe, _now())
    assert isinstance(providers.classifier, RulesClassifierProvider)
    assert not isinstance(providers.classifier, FixtureClassifierProvider)
    # data is always real for a custom universe
    assert providers.modes == {
        "rss": "real",
        "edgar": "real",
        "news": "real",
        "quotes": "real",
        "email": "fixture",
    }


def test_manifest_marks_custom_universes(tmp_path):
    brief = _stub_brief("user-foo", "Foo")
    write_artifacts(brief, tmp_path, default=False, custom=True)
    builtin = _stub_brief("fintech", "Fintech")
    write_artifacts(builtin, tmp_path, default=True, custom=False)

    manifest = json.loads((tmp_path / "universes.json").read_text())
    by_id = {m["id"]: m for m in manifest}
    assert by_id["user-foo"]["custom"] is True
    assert by_id["fintech"]["custom"] is False


# -- helpers --


def _now():
    from datetime import datetime, timezone

    return datetime(2026, 6, 10, 13, 45, tzinfo=timezone.utc)


def _stub_brief(uid: str, label: str) -> DailyBrief:
    return DailyBrief(
        universe_id=uid,
        generated_at=_now(),
        market_open_at=_now(),
        tldr="",
        counts=Counts(total_items=0, hot_items=0),
        market=[],
        priority_signals=[],
        by_company={},
        sector_headlines=[],
        source_status=[],
        universe_label=label,
        subject_ticker="AAPL",
        subject_name="Apple",
        categories=["Product", "Financial"],
        display_tz="America/New_York",
        classifier_engine="rules",
    )
