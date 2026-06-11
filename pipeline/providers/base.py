"""Vendor-neutral provider interfaces.

Every external dependency (RSS, SEC EDGAR, news search, stock quotes, email
send, Claude) sits behind one of these typed interfaces. The app runs
end-to-end with zero API keys against the Fixture* reference implementations;
real providers are added by implementing the same interface (see
real_stubs.py and anthropic_classifier.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from pipeline.contracts import EmailReceipt, Quote, RawItem, UniverseConfig
from pipeline.contracts.universe import RSSFeed
from pipeline.contracts.models import Classification


class RSSProvider(ABC):
    @abstractmethod
    def fetch(self, feeds: list[RSSFeed]) -> list[RawItem]:
        """Pull recent entries from the configured feeds."""


class EdgarProvider(ABC):
    @abstractmethod
    def fetch(self, tickers: list[str]) -> list[RawItem]:
        """8-Ks + press exhibits via the SEC submissions API."""


class NewsProvider(ABC):
    @abstractmethod
    def search(self, tickers: list[str], sector_keywords: list[str]) -> list[RawItem]:
        """Keyword/ticker news search."""


class QuoteProvider(ABC):
    @abstractmethod
    def snapshot(self, tickers: list[str]) -> list[Quote]:
        """Pre-market last/%chg/volume/avg_volume snapshot (rvol derived later)."""


class ClassifierResult(BaseModel):
    """What the process stage hands back to the orchestrator."""

    tldr: str
    classifications: list[Classification]
    engine: str  # "fixture" | "rules" | "anthropic" — provenance only


class ClassifierProvider(ABC):
    """The brain. Real implementation = ONE batched Anthropic API call per run.

    Implementations must never raise on bad model output: validate, retry
    once, then fall back to rule-based tagging (see rules.py). A run never
    crashes because of the LLM.
    """

    @abstractmethod
    def classify(self, items: list[RawItem], universe: UniverseConfig) -> ClassifierResult: ...


class EmailProvider(ABC):
    @abstractmethod
    def send(self, recipients: list[str], subject: str, html: str) -> EmailReceipt: ...
