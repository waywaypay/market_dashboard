"""Claude-via-Venice classifier: speaks Venice's OpenAI-compatible endpoint and
reconciles output exactly like the Anthropic path. Tested against a mocked httpx
transport — no network, no key — plus the never-crash fallback contract."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from pipeline.contracts import RawItem
from pipeline.contracts.universe import load_universe
from pipeline.evals.harness import UNIVERSES_DIR
from pipeline.providers.base import ClassifierProvider
from pipeline.providers.venice_classifier import VeniceClassifierProvider

NOW = datetime(2026, 6, 10, 13, 45, tzinfo=timezone.utc)


@pytest.fixture
def universe():
    return load_universe(UNIVERSES_DIR / "diagnostics.yaml")


def _items() -> list[RawItem]:
    return [
        RawItem(
            id="v1", source="news", feed="Reuters", url="https://r/1",
            title="Veracyte wins expanded Medicare coverage for Decipher",
            raw_text="CMS will reimburse the prostate test.", ts=NOW, ticker_guess="VCYT",
        ),
        RawItem(
            id="v2", source="rss", feed="STAT", url="https://s/2",
            title="WHO director-general concerned after Ebola outbreak visit",
            raw_text="Global health news with no diagnostics-company angle.", ts=NOW,
        ),
    ]


def _venice_response(batch: dict) -> httpx.Response:
    """Shape Venice/OpenAI returns: choices[0].message.content is the JSON string."""
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(batch)}}]},
    )


def test_subclasses_interface() -> None:
    assert issubclass(VeniceClassifierProvider, ClassifierProvider)


def test_classifies_via_mocked_venice(universe) -> None:
    items = _items()
    batch = {
        "tldr": "Veracyte gains Medicare coverage; the rest is off-topic noise.",
        "classifications": [
            {"item_id": "v1", "ticker": "VCYT", "category": "Regulatory",
             "materiality": 5, "summary": "Medicare covers Decipher.",
             "is_subject_relevant": True},
            {"item_id": "v2", "ticker": None, "category": "Clinical",
             "materiality": 1, "summary": "Global health item, immaterial.",
             "is_subject_relevant": False},
        ],
    }
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["model"] = json.loads(req.content)["model"]
        captured["auth"] = req.headers.get("authorization")
        return _venice_response(batch)

    provider = VeniceClassifierProvider(
        api_key="vk-test", transport=httpx.MockTransport(handler)
    )
    result = provider.classify(items, universe)

    assert result.engine == "venice"
    assert captured["url"] == "https://api.venice.ai/api/v1/chat/completions"
    assert captured["model"] == "claude-sonnet-4-6"  # default Claude model
    assert captured["auth"] == "Bearer vk-test"
    by_id = {c.item_id: c for c in result.classifications}
    assert by_id["v1"].materiality == 5 and by_id["v1"].is_subject_relevant
    assert by_id["v2"].materiality == 1 and not by_id["v2"].is_subject_relevant
    assert result.tldr


def test_model_is_overridable(universe) -> None:
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["model"] = json.loads(req.content)["model"]
        return _venice_response(
            {"tldr": "x", "classifications": [
                {"item_id": "v1", "ticker": "VCYT", "category": "Regulatory",
                 "materiality": 3, "summary": "s", "is_subject_relevant": True}]}
        )

    provider = VeniceClassifierProvider(
        model="claude-opus-4-6", api_key="vk-test",
        transport=httpx.MockTransport(handler),
    )
    provider.classify(_items()[:1], universe)
    assert captured["model"] == "claude-opus-4-6"


def test_falls_back_to_rules_on_http_error(universe) -> None:
    provider = VeniceClassifierProvider(
        api_key="vk-test", max_retries=1,
        transport=httpx.MockTransport(lambda req: httpx.Response(500, text="boom")),
    )
    result = provider.classify(_items(), universe)
    assert result.engine.startswith("rules (venice failed")
    assert len(result.classifications) == 2  # total: every item still classified


def test_missing_key_falls_back_without_network(universe) -> None:
    def boom(req: httpx.Request) -> httpx.Response:  # must never be called
        raise AssertionError("no HTTP call should happen without a key")

    provider = VeniceClassifierProvider(
        api_key=None, transport=httpx.MockTransport(boom)
    )
    result = provider.classify(_items(), universe)
    assert result.engine == "rules (venice key missing)"
    assert len(result.classifications) == 2


def test_empty_items_short_circuits(universe) -> None:
    provider = VeniceClassifierProvider(api_key="vk-test")
    result = provider.classify([], universe)
    assert result.engine == "venice" and result.classifications == []
