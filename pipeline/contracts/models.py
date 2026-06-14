"""Cross-stage data contracts.

Every stage of the pipeline communicates exclusively through these models.
`DailyBrief` is the output artifact consumed by both the web dashboard
(`web/public/brief.json`) and the email renderer, so the two can never
disagree.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

SourceKind = Literal["rss", "edgar", "news", "quotes"]


class RawItem(BaseModel):
    """A single unprocessed story/filing from any source provider."""

    id: str
    source: SourceKind
    feed: Optional[str] = None  # human label, e.g. "GenomeWeb" or "EDGAR 8-K"
    url: str
    title: str
    raw_text: str
    ts: datetime
    ticker_guess: Optional[str] = None  # None => possibly sector-wide


class Quote(BaseModel):
    """Pre-market snapshot for one ticker, enriched by the fuse stage."""

    ticker: str
    name: str
    last: float
    chg_pct: float  # % change vs prior close
    volume: int
    avg_volume: int
    sigma: float  # trailing daily %-move stdev, supplied by the quote provider
    rvol: Optional[float] = None  # derived: volume / avg_volume
    flagged: bool = False  # unusual move (set by fuse stage)
    flag_reason: Optional[str] = None  # "sigma", "rvol" or "sigma+rvol"
    driver_item_id: Optional[str] = None  # most likely news driver (fuse stage)


class Classification(BaseModel):
    """Per-item output of the process (Claude) stage."""

    item_id: str
    ticker: Optional[str] = None  # None => sector-wide story
    category: str  # must be one of the universe's configured categories
    materiality: int = Field(ge=1, le=5)
    summary: str  # 1-2 sentences in the universe's house_style
    is_subject_relevant: bool


class ClassificationBatch(BaseModel):
    """The strict-JSON shape returned by the single batched LLM call."""

    tldr: str  # 1-line synthesis across the whole set
    classifications: list[Classification]


class PriceReaction(BaseModel):
    """Badge attached to an item: did its ticker move, how much, on what RVOL."""

    ticker: str
    chg_pct: float
    rvol: Optional[float] = None
    flagged: bool = False


class Item(BaseModel):
    """A classified, summarized, price-linked story as it appears in the brief."""

    id: str
    ticker: Optional[str] = None
    company: Optional[str] = None  # display name (peers, subject, private watch)
    category: str
    materiality: int = Field(ge=1, le=5)
    summary: str
    title: str
    url: str
    source: str  # feed label, e.g. "GenomeWeb" / "EDGAR 8-K"
    ts: datetime
    is_subject_relevant: bool
    price_reaction: Optional[PriceReaction] = None
    is_driver: bool = False  # attributed driver of an unusual move


class SourceHealth(BaseModel):
    provider: SourceKind
    status: Literal["ok", "stale", "failed"]
    last_ts: Optional[datetime] = None  # newest data timestamp seen this run
    detail: Optional[str] = None  # written in the product's own voice


class PricePoint(BaseModel):
    """One daily close for the historical price-overlay chart. Keys are kept
    short (`d`, `c`) because this ships ~60 sessions x every ticker."""

    d: str  # ISO date, YYYY-MM-DD
    c: float  # close


class Counts(BaseModel):
    total_items: int
    hot_items: int  # materiality >= thresholds.hot_materiality


class EmailReceipt(BaseModel):
    accepted: bool
    detail: str  # fixture: path the .html was written to


class DailyBrief(BaseModel):
    """The output artifact. Dashboard and email both render from this."""

    universe_id: str
    generated_at: datetime
    market_open_at: datetime
    tldr: str
    counts: Counts
    market: list[Quote]  # subject pinned first
    priority_signals: list[Item]  # material + price-linked, materiality desc
    by_company: dict[str, list[Item]]  # subject first, then peers, then watch
    sector_headlines: list[Item]  # not tied to a single comp
    source_status: list[SourceHealth]

    # -- presentation metadata (extends the minimum contract so the web app
    #    renders entirely from the artifact: no config files ship to the UI) --
    universe_label: str
    subject_ticker: str
    subject_name: str
    categories: list[str]  # ordered; UI maps index -> category color
    display_tz: str  # IANA tz for rendering timestamps (delivery.tz)
    classifier_engine: str  # "fixture" | "rules" | "anthropic" (provenance)

    # -- data provenance: which providers actually produced this brief. The
    #    dashboard banners anything that is not fully real, so synthetic demo
    #    data can never pass for live market data. Defaults are the honest
    #    direction: an artifact predating these fields reads as fixture. --
    data_mode: Literal["real", "fixture", "mixed"] = "fixture"
    provider_modes: dict[str, str] = Field(default_factory=dict)  # source -> fixture|real

    # -- historical daily closes per ticker for the overlay chart. Best-effort
    #    and presentation-only (empty when no history source is reachable);
    #    ascending by date. Defaults empty so older artifacts still validate. --
    history: dict[str, list[PricePoint]] = Field(default_factory=dict)
