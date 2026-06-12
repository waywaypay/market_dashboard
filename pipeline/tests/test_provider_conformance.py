"""Interface conformance: every provider (fixture + real + stub) implements the
vendor-neutral contract with the right shape. The remaining stub raises
NotImplementedError but must still satisfy the interface and accept the right
argument types — so a real integration slots in without touching any stage.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pipeline.contracts import EmailReceipt, Quote, RawItem
from pipeline.providers.base import (
    EdgarProvider,
    EmailProvider,
    NewsProvider,
    QuoteProvider,
    RSSProvider,
)
from pipeline.providers.fixture import (
    FixtureClassifierProvider,
    FixtureEdgarProvider,
    FixtureEmailProvider,
    FixtureNewsProvider,
    FixtureQuoteProvider,
    FixtureRSSProvider,
    RulesClassifierProvider,
    _load,
)
from pipeline.providers.edgar import SecEdgarProvider
from pipeline.providers.exa_news import ExaNewsProvider
from pipeline.providers.fallback import FallbackQuoteProvider
from pipeline.providers.real_stubs import SmtpEmailProvider
from pipeline.providers.rss import HttpRSSProvider
from pipeline.providers.stooq_quotes import StooqQuoteProvider
from pipeline.providers.yahoo_quotes import YahooQuoteProvider
from pipeline.contracts.universe import RSSFeed

NOW = datetime(2026, 6, 10, 13, 45, tzinfo=timezone.utc)


def test_fixture_providers_subclass_interfaces() -> None:
    assert issubclass(FixtureRSSProvider, RSSProvider)
    assert issubclass(FixtureEdgarProvider, EdgarProvider)
    assert issubclass(FixtureNewsProvider, NewsProvider)
    assert issubclass(FixtureQuoteProvider, QuoteProvider)
    assert issubclass(FixtureEmailProvider, EmailProvider)


def test_real_providers_subclass_interfaces() -> None:
    # implemented integrations slot in behind the same vendor-neutral contracts
    assert issubclass(HttpRSSProvider, RSSProvider)
    assert issubclass(SecEdgarProvider, EdgarProvider)
    assert issubclass(ExaNewsProvider, NewsProvider)
    assert issubclass(YahooQuoteProvider, QuoteProvider)
    assert issubclass(StooqQuoteProvider, QuoteProvider)
    assert issubclass(FallbackQuoteProvider, QuoteProvider)
    # remaining stub still satisfies the interface (vendor TBD)
    assert issubclass(SmtpEmailProvider, EmailProvider)


def test_fixture_rss_returns_rawitems() -> None:
    items = FixtureRSSProvider("diagnostics", NOW).fetch([RSSFeed(label="GenomeWeb")])
    assert items and all(isinstance(i, RawItem) for i in items)
    assert all(i.source == "rss" for i in items)


def test_fixture_quotes_return_quotes() -> None:
    quotes = FixtureQuoteProvider("diagnostics", NOW).snapshot(["VCYT", "NTRA"])
    assert quotes and all(isinstance(q, Quote) for q in quotes)
    assert {q.ticker for q in quotes} == {"VCYT", "NTRA"}


def test_fixture_email_writes_html(tmp_path) -> None:
    provider = FixtureEmailProvider(out_dir=tmp_path)
    receipt = provider.send(["a@b.com"], "Subject Line", "<html>hi</html>")
    assert isinstance(receipt, EmailReceipt) and receipt.accepted
    assert list(tmp_path.glob("*.html"))


def test_rules_classifier_is_total(tmp_path) -> None:
    """The fallback classifier handles arbitrary input without raising."""
    from pipeline.contracts.universe import load_universe
    from pipeline.evals.harness import UNIVERSES_DIR

    universe = load_universe(UNIVERSES_DIR / "diagnostics.yaml")
    items = [
        RawItem(
            id="x1", source="news", feed="z", url="https://z/1",
            title="Some unexpected headline", raw_text="Body text with no keywords.",
            ts=NOW, ticker_guess=None,
        )
    ]
    result = RulesClassifierProvider().classify(items, universe)
    assert len(result.classifications) == 1
    assert result.classifications[0].category in universe.categories


def test_fixture_classifier_composes_tldr_when_no_canned_items_match() -> None:
    """Real items (ids absent from the canned table) must never inherit the
    synthetic fixture tldr — that would headline fabricated events."""
    from pipeline.contracts.universe import load_universe
    from pipeline.evals.harness import UNIVERSES_DIR

    universe = load_universe(UNIVERSES_DIR / "diagnostics.yaml")
    item = RawItem(
        id="real-pull-001", source="news", feed="Reuters", url="https://r/1",
        title="Veracyte wins expanded Medicare coverage", raw_text="Coverage news.",
        ts=NOW, ticker_guess="VCYT",
    )
    result = FixtureClassifierProvider("diagnostics").classify([item], universe)
    canned_tldr = _load("diagnostics", "classifications.json")["tldr"]
    assert result.tldr.strip() and result.tldr != canned_tldr


def test_remaining_stub_raises_actionable_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        SmtpEmailProvider().send(["a@b.com"], "subj", "<html></html>")
