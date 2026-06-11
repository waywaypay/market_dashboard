"""Provider selection. Deterministic, env-driven — never decided by an LLM.

    BRIEF_PROVIDERS  fixture (default) | real      — source + email providers
    BRIEF_CLASSIFIER auto (default) | fixture | rules | anthropic

"auto" uses Anthropic when ANTHROPIC_API_KEY is set and the SDK is installed,
otherwise the fixture classifier — so the project runs with zero keys and
upgrades itself when a key appears.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from datetime import datetime

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


@dataclass(frozen=True)
class ProviderSet:
    rss: RSSProvider
    edgar: EdgarProvider
    news: NewsProvider
    quotes: QuoteProvider
    classifier: ClassifierProvider
    email: EmailProvider


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


def build_providers(universe_id: str, now: datetime) -> ProviderSet:
    mode = os.environ.get("BRIEF_PROVIDERS", "fixture").lower()
    if mode == "fixture":
        return ProviderSet(
            rss=FixtureRSSProvider(universe_id, now),
            edgar=FixtureEdgarProvider(universe_id, now),
            news=FixtureNewsProvider(universe_id, now),
            quotes=FixtureQuoteProvider(universe_id, now),
            classifier=build_classifier(universe_id),
            email=FixtureEmailProvider(),
        )
    if mode == "real":
        from pipeline.providers.real_stubs import (
            HttpRSSProvider,
            MarketDataQuoteProvider,
            SearchNewsProvider,
            SecEdgarProvider,
            SmtpEmailProvider,
        )

        return ProviderSet(
            rss=HttpRSSProvider(),
            edgar=SecEdgarProvider(),
            news=SearchNewsProvider(),
            quotes=MarketDataQuoteProvider(),
            classifier=build_classifier(universe_id),
            email=SmtpEmailProvider(),
        )
    raise ValueError(f"Unknown BRIEF_PROVIDERS={mode!r}")
