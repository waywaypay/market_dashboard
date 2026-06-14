"""First Read provider: the deterministic composer, the Venice integration
(wire format + total failure handling), and registry selection.

Like the classifier, the contract is that the run never crashes because of the
LLM: a bad/empty/erroring Venice response degrades to the composer, and the
brief always ships a note. The Venice tests drive httpx via MockTransport, so
they exercise the real request/response shaping with no network.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from pipeline.contracts import Counts, DailyBrief, Item, Quote
from pipeline.contracts.universe import load_universe
from pipeline.evals.harness import UNIVERSES_DIR
from pipeline.providers.base import FirstReadProvider, FirstReadResult
from pipeline.providers.fixture import FixtureFirstReadProvider, compose_first_read
from pipeline.providers.venice_first_read import (
    VENICE_CHAT_URL,
    VeniceFirstReadProvider,
    api_key_from_env,
)

NOW = datetime(2026, 6, 10, 13, 45, tzinfo=timezone.utc)


def _universe():
    return load_universe(UNIVERSES_DIR / "diagnostics.yaml")


def _brief() -> DailyBrief:
    """A minimal-but-realistic assembled brief: one flagged mover, one signal."""
    return DailyBrief(
        universe_id="diagnostics",
        generated_at=NOW,
        market_open_at=NOW,
        tldr="VCYT jumps on a Medicare win; the rest of the tape is mixed.",
        counts=Counts(total_items=4, hot_items=2),
        market=[
            Quote(
                ticker="VCYT", name="Veracyte", last=40.0, chg_pct=6.2, volume=3_000_000,
                avg_volume=1_000_000, sigma=0.03, rvol=3.1, flagged=True,
                flag_reason="sigma+rvol",
            ),
            Quote(
                ticker="NTRA", name="Natera", last=80.0, chg_pct=-0.8, volume=1, avg_volume=1,
                sigma=0.03,
            ),
        ],
        priority_signals=[
            Item(
                id="i1", ticker="VCYT", company="Veracyte", category="reimbursement",
                materiality=5, summary="Wins expanded Medicare coverage for its core test",
                title="Veracyte Medicare win", url="https://example.com/1", source="Reuters",
                ts=NOW, is_subject_relevant=True,
            )
        ],
        by_company={},
        sector_headlines=[],
        source_status=[],
        universe_label="Diagnostics & Genomics",
        subject_ticker="VCYT",
        subject_name="Veracyte",
        categories=["reimbursement"],
        display_tz="America/New_York",
        classifier_engine="fixture",
    )


# --- composer / fixture provider --------------------------------------------


def test_compose_first_read_is_nonempty_and_grounded() -> None:
    text = compose_first_read(_brief())
    assert text.strip()
    # It speaks from the brief: the flagged mover and the top signal show up.
    assert "VCYT" in text
    assert "Medicare" in text
    # It is a distinct note, not a verbatim echo of the one-line tldr.
    assert text != _brief().tldr


def test_compose_first_read_handles_a_quiet_tape() -> None:
    brief = _brief()
    for q in brief.market:
        q.flagged = False
    brief.priority_signals = []
    text = compose_first_read(brief)
    assert "calm" in text.lower()
    assert "4 items" in text  # counts line still present


def test_fixture_first_read_provider() -> None:
    result = FixtureFirstReadProvider().generate(_brief(), _universe())
    assert isinstance(result, FirstReadResult)
    assert result.engine == "fixture"
    assert result.text.strip()


# --- Venice integration ------------------------------------------------------


def _venice(handler, **kw) -> VeniceFirstReadProvider:
    return VeniceFirstReadProvider(
        api_key="test-key", transport=httpx.MockTransport(handler), **kw
    )


def test_venice_parses_response_and_sends_right_request() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "  Markets steady; VCYT leads on coverage.  "}}]},
        )

    result = _venice(handler).generate(_brief(), _universe())
    assert result.engine == "venice"
    assert result.text == "Markets steady; VCYT leads on coverage."  # trimmed
    assert seen["url"] == VENICE_CHAT_URL
    assert seen["auth"] == "Bearer test-key"
    # OpenAI-compatible body with a system + user message, and Venice's own
    # system prompt suppressed so our house voice controls the output.
    assert seen["body"]["model"]
    roles = [m["role"] for m in seen["body"]["messages"]]
    assert roles == ["system", "user"]
    assert seen["body"]["venice_parameters"]["include_venice_system_prompt"] is False


def test_venice_falls_back_to_composer_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream boom")

    result = _venice(handler).generate(_brief(), _universe())
    assert result.text == compose_first_read(_brief())  # never blank
    assert result.engine.startswith("fixture")
    assert "venice failed" in result.engine


def test_venice_retries_once_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"choices": [{"message": {"content": "Recovered note."}}]})

    result = _venice(handler).generate(_brief(), _universe())
    assert calls["n"] == 2
    assert result.engine == "venice"
    assert result.text == "Recovered note."


def test_venice_empty_content_falls_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

    result = _venice(handler).generate(_brief(), _universe())
    assert result.engine.startswith("fixture")
    assert result.text == compose_first_read(_brief())


def test_venice_without_key_uses_composer_and_never_calls_out() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # must not be hit
        raise AssertionError("Venice must not be called without a key")

    provider = VeniceFirstReadProvider(api_key=None, transport=httpx.MockTransport(handler))
    result = provider.generate(_brief(), _universe())
    assert result.engine.startswith("fixture")
    assert result.text.strip()


# --- registry selection ------------------------------------------------------


def test_registry_first_read_selection(monkeypatch) -> None:
    from pipeline.providers import registry

    # explicit fixture
    monkeypatch.setenv("BRIEF_FIRST_READ", "fixture")
    assert isinstance(registry.build_first_read(), FixtureFirstReadProvider)

    # explicit venice
    monkeypatch.setenv("BRIEF_FIRST_READ", "venice")
    assert isinstance(registry.build_first_read(), VeniceFirstReadProvider)

    # unknown mode is a loud error, not a silent default
    monkeypatch.setenv("BRIEF_FIRST_READ", "bogus")
    with pytest.raises(ValueError):
        registry.build_first_read()


def test_registry_auto_uses_key_presence(monkeypatch) -> None:
    from pipeline.providers import registry

    monkeypatch.setenv("BRIEF_FIRST_READ", "auto")
    # auto resolves on the (normalized) key; patch the detector so the test does
    # not depend on the ambient environment.
    monkeypatch.setattr(
        "pipeline.providers.venice_first_read.api_key_from_env", lambda: None
    )
    assert isinstance(registry.build_first_read(), FixtureFirstReadProvider)

    monkeypatch.setattr(
        "pipeline.providers.venice_first_read.api_key_from_env", lambda: "k"
    )
    assert isinstance(registry.build_first_read(), VeniceFirstReadProvider)


def test_first_read_provider_is_abstract() -> None:
    assert issubclass(FixtureFirstReadProvider, FirstReadProvider)
    assert issubclass(VeniceFirstReadProvider, FirstReadProvider)


def test_api_key_from_env_matches_flexible_names(monkeypatch) -> None:
    monkeypatch.setenv("VENICE_AI_API_KEY", "  abc  ")
    assert api_key_from_env() == "abc"
