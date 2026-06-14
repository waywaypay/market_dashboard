"""Provider selection. Deterministic, env-driven — never decided by an LLM.

    BRIEF_PROVIDERS  fixture (default) | real     — global default for all sources
                     (the production server, pipeline/serve.py, defaults
                     itself to real so deploys never silently serve fixtures)
    BRIEF_RSS / BRIEF_EDGAR / BRIEF_NEWS / BRIEF_QUOTES / BRIEF_EMAIL
                     fixture | real               — per-provider override
    BRIEF_CLASSIFIER auto (default) | fixture | rules | anthropic

Real implementations exist for RSS (feed URLs in the universe YAML), EDGAR
(free; set SEC_EDGAR_USER_AGENT per SEC fair-access policy), news search
(Exa; set EXA_API_KEY) and quotes (Yahoo Finance chart API; no key). Email
transport is still a stub — keep it on fixtures while running real data:

    BRIEF_PROVIDERS=real BRIEF_EMAIL=fixture make run-pipeline

"auto" classification uses Anthropic when ANTHROPIC_API_KEY is set and the SDK
is installed, otherwise the fixture classifier — so the project always runs
with zero keys and upgrades itself when keys appear.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, TypeVar

from pipeline.contracts import UniverseConfig
from pipeline.providers.base import (
    ClassifierProvider,
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
)

T = TypeVar("T")


@dataclass(frozen=True)
class ProviderSet:
    rss: RSSProvider
    edgar: EdgarProvider
    news: NewsProvider
    quotes: QuoteProvider
    classifier: ClassifierProvider
    email: EmailProvider
    # source -> "fixture"|"real", recorded at build time so the brief can
    # carry its own provenance (DailyBrief.data_mode / provider_modes)
    modes: dict[str, str]


def _anthropic_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and (
        importlib.util.find_spec("anthropic") is not None
    )


def build_classifier(universe_id: str) -> ClassifierProvider:
    mode = os.environ.get("BRIEF_CLASSIFIER", "auto").lower()
    if mode == "auto":
        mode = "anthropic" if _anthropic_available() else "fixture"
    if mode == "anthropic":
        from pipeline.providers.anthropic_classifier import AnthropicClassifierProvider

        return AnthropicClassifierProvider()
    if mode == "rules":
        return RulesClassifierProvider()
    if mode == "fixture":
        return FixtureClassifierProvider(universe_id)
    raise ValueError(f"Unknown BRIEF_CLASSIFIER={mode!r}")


def _mode(name: str) -> str:
    default = os.environ.get("BRIEF_PROVIDERS", "fixture").lower()
    mode = os.environ.get(f"BRIEF_{name}", default).lower()
    if mode not in ("fixture", "real"):
        raise ValueError(f"Unknown BRIEF_{name}={mode!r} (expected fixture|real)")
    return mode


def _pick(name: str, fixture: Callable[[], T], real: Callable[[], T]) -> T:
    return fixture() if _mode(name) == "fixture" else real()


def build_providers(universe: UniverseConfig, now: datetime) -> ProviderSet:
    uid = universe.id

    def real_rss() -> RSSProvider:
        from pipeline.providers.rss import HttpRSSProvider

        return HttpRSSProvider(companies=universe.companies)

    def real_edgar() -> EdgarProvider:
        from pipeline.providers.edgar import SecEdgarProvider

        return SecEdgarProvider()

    def real_news() -> NewsProvider:
        from pipeline.providers.exa_news import ExaNewsProvider

        return ExaNewsProvider(companies=universe.companies, watch=universe.private_watch)

    def real_quotes() -> QuoteProvider:
        from pipeline.providers.fallback import FallbackQuoteProvider
        from pipeline.providers.stooq_quotes import StooqQuoteProvider
        from pipeline.providers.yahoo_quotes import YahooQuoteProvider

        # Yahoo first (pre-market tape); Stooq is the keyless real-data
        # fallback for when Yahoo rate-limits the host's shared egress IP.
        # Stooq takes the run clock so its as-of-close move tracks `now`.
        chain: list[QuoteProvider] = [
            YahooQuoteProvider(companies=universe.companies),
            StooqQuoteProvider(companies=universe.companies, now=now),
        ]
        # Last resort: a keyed vendor that answers from cloud IPs the keyless
        # tiers are blocked on. Only joins the chain when a key is present.
        if os.environ.get("ALPHAVANTAGE_API_KEY"):
            from pipeline.providers.alphavantage_quotes import AlphaVantageQuoteProvider

            chain.append(AlphaVantageQuoteProvider(companies=universe.companies, now=now))
        return FallbackQuoteProvider(*chain)

    def real_email() -> EmailProvider:
        from pipeline.providers.real_stubs import SmtpEmailProvider

        return SmtpEmailProvider()

    return ProviderSet(
        rss=_pick("RSS", lambda: FixtureRSSProvider(uid, now), real_rss),
        edgar=_pick("EDGAR", lambda: FixtureEdgarProvider(uid, now), real_edgar),
        news=_pick("NEWS", lambda: FixtureNewsProvider(uid, now), real_news),
        quotes=_pick("QUOTES", lambda: FixtureQuoteProvider(uid, now), real_quotes),
        classifier=build_classifier(uid),
        email=_pick("EMAIL", lambda: FixtureEmailProvider(), real_email),
        modes={name.lower(): _mode(name) for name in ("RSS", "EDGAR", "NEWS", "QUOTES", "EMAIL")},
    )
