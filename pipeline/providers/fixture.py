"""FixtureProvider reference implementations.

Synthetic, deterministic, zero-API-key. Fixture timestamps are stored as
minute offsets relative to "now" so the demo always reads like this morning
(and the look-ahead guard has a future-dated item to drop).

Files, per universe (pipeline/fixtures/<universe_id>/):
    raw_items.json        {"items": [{... "provider": "rss", "ts_offset_min": -94 ...}]}
    quotes.json           {"quotes": [{ticker, name, last, chg_pct, volume, avg_volume, sigma}]}
    classifications.json  {"tldr": "...", "by_item": {"<id>": {category, materiality, ...}}}
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from pipeline.contracts import DailyBrief, EmailReceipt, Quote, RawItem, UniverseConfig
from pipeline.contracts.models import Classification
from pipeline.contracts.universe import RSSFeed
from pipeline.providers import rules
from pipeline.providers.base import (
    ClassifierProvider,
    ClassifierResult,
    EdgarProvider,
    EmailProvider,
    FirstReadProvider,
    FirstReadResult,
    NewsProvider,
    QuoteProvider,
    RSSProvider,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def _load(universe_id: str, name: str) -> dict:
    path = FIXTURES_DIR / universe_id / name
    if not path.exists():
        raise FileNotFoundError(
            f"No fixture {name!r} for universe {universe_id!r} — expected {path}. "
            "Add fixtures or run with real providers."
        )
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _raw_items(universe_id: str, source: str, now: datetime) -> list[RawItem]:
    rows = _load(universe_id, "raw_items.json")["items"]
    out: list[RawItem] = []
    for row in rows:
        if row["provider"] != source:
            continue
        out.append(
            RawItem(
                id=row["id"],
                source=source,  # type: ignore[arg-type]
                feed=row.get("feed"),
                url=row["url"],
                title=row["title"],
                raw_text=row["raw_text"],
                ts=now + timedelta(minutes=row["ts_offset_min"]),
                ticker_guess=row.get("ticker_guess"),
            )
        )
    return out


class FixtureRSSProvider(RSSProvider):
    def __init__(self, universe_id: str, now: datetime):
        self.universe_id, self.now = universe_id, now

    def fetch(self, feeds: list[RSSFeed]) -> list[RawItem]:
        labels = {f.label for f in feeds}
        items = _raw_items(self.universe_id, "rss", self.now)
        return [i for i in items if i.feed in labels] or items


class FixtureEdgarProvider(EdgarProvider):
    def __init__(self, universe_id: str, now: datetime):
        self.universe_id, self.now = universe_id, now

    def fetch(self, tickers: list[str]) -> list[RawItem]:
        return [
            i
            for i in _raw_items(self.universe_id, "edgar", self.now)
            if i.ticker_guess is None or i.ticker_guess in tickers
        ]


class FixtureNewsProvider(NewsProvider):
    def __init__(self, universe_id: str, now: datetime):
        self.universe_id, self.now = universe_id, now

    def search(self, tickers: list[str], sector_keywords: list[str]) -> list[RawItem]:
        return _raw_items(self.universe_id, "news", self.now)


class FixtureQuoteProvider(QuoteProvider):
    def __init__(self, universe_id: str, now: datetime):
        self.universe_id, self.now = universe_id, now

    def snapshot(self, tickers: list[str]) -> list[Quote]:
        rows = _load(self.universe_id, "quotes.json")["quotes"]
        by_ticker = {r["ticker"]: r for r in rows}
        return [Quote(**by_ticker[t]) for t in tickers if t in by_ticker]


class FixtureClassifierProvider(ClassifierProvider):
    """Canned classifications keyed by item id; rule-based for unseen items.

    This is the reference implementation of the process stage's contract —
    the eval gate compares its output (and, when a key is present, real
    Claude's) against the gold labels in pipeline/evals/gold/.
    """

    def __init__(self, universe_id: str):
        self.universe_id = universe_id

    def classify(self, items: list[RawItem], universe: UniverseConfig) -> ClassifierResult:
        data = _load(self.universe_id, "classifications.json")
        by_item: dict[str, dict] = data["by_item"]
        out: list[Classification] = []
        canned_hits = 0
        for item in items:
            canned = by_item.get(item.id)
            if canned is not None:
                canned_hits += 1
                out.append(Classification(item_id=item.id, **canned))
            else:
                out.append(rules.classify_item(item, universe))
        # The canned tldr describes the fixture stories. If nothing matched
        # (e.g. real sources with no ANTHROPIC_API_KEY), it would headline
        # synthetic events over real items — compose deterministically instead.
        tldr = data["tldr"] if canned_hits else compose_tldr_fallback(out, universe)
        return ClassifierResult(tldr=tldr, classifications=out, engine="fixture")


class RulesClassifierProvider(ClassifierProvider):
    """Pure rule-based classifier — also the LLM-failure fallback path."""

    def classify(self, items: list[RawItem], universe: UniverseConfig) -> ClassifierResult:
        classifications = rules.classify_batch(items, universe)
        return ClassifierResult(
            tldr=compose_tldr_fallback(classifications, universe),
            classifications=classifications,
            engine="rules",
        )


def compose_tldr_fallback(classifications: list[Classification], universe: UniverseConfig) -> str:
    """Deterministic 1-liner when no LLM tldr is available."""
    hot = [c for c in classifications if c.materiality >= universe.thresholds.hot_materiality]
    if not hot:
        return f"Quiet tape across {universe.label}: nothing material before the open."
    top = sorted(hot, key=lambda c: (-c.materiality, c.item_id))[0]
    names = ", ".join(
        sorted({c.ticker or "sector" for c in hot if c.item_id != top.item_id})
    )
    lead = top.summary.rstrip(".")
    return f"{lead}. Also hot: {names}." if names else f"{lead}."


def compose_first_read(brief: DailyBrief) -> str:
    """Deterministic narrative morning note when no LLM is available.

    Reads off the already-assembled brief (the same object the dashboard
    renders), so it can never headline anything the brief doesn't carry. Built
    from the flagged movers and the top priority signals rather than echoing
    `tldr`, so it reads as a distinct note next to The Read band.
    """
    movers = [q for q in brief.market if q.flagged]
    parts: list[str] = []
    if movers:
        moves = ", ".join(f"{q.ticker} {q.chg_pct:+.1f}%" for q in movers[:4])
        verb = "is" if len(movers) == 1 else "are"
        parts.append(
            f"Pre-market, {moves} {verb} moving on unusual volume — "
            "the moves to explain before the bell."
        )
    else:
        parts.append(
            "The tape is calm pre-market: no unusual moves are flagged across the set."
        )
    top = brief.priority_signals[:2]
    if top:
        bites = "; ".join(
            f"{(s.company or s.ticker or 'Sector')} — {s.summary.strip().rstrip('.')}"
            for s in top
        )
        parts.append(f"Leading the read: {bites}.")
    parts.append(
        f"{brief.counts.total_items} items cleared the floor "
        f"({brief.counts.hot_items} hot) ahead of the open."
    )
    return " ".join(parts)


class FixtureFirstReadProvider(FirstReadProvider):
    """Keyless reference implementation of the First Read contract, and the
    fallback the Venice provider drops to when its call fails."""

    def generate(self, brief: DailyBrief, universe: UniverseConfig) -> FirstReadResult:
        return FirstReadResult(text=compose_first_read(brief), engine="fixture")


class FixtureEmailProvider(EmailProvider):
    """Writes the rendered .html to disk instead of sending."""

    def __init__(self, out_dir: str | Path = "out/emails"):
        self.out_dir = Path(out_dir)

    def send(self, recipients: list[str], subject: str, html: str) -> EmailReceipt:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in subject)[:80]
        path = self.out_dir / f"{safe}.html"
        path.write_text(html, encoding="utf-8")
        to = ", ".join(recipients) if recipients else "(no recipients configured)"
        return EmailReceipt(accepted=True, detail=f"written to {path} — would send to {to}")
