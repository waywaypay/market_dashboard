"""Source stage: parallel fetch -> look-ahead filter -> dedupe -> rvol.

Pure given its inputs: (universe, providers, now) -> SourceResult.
Provider failures never crash the run; they surface as SourceHealth(failed).
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from pydantic import BaseModel

from pipeline.contracts import Quote, RawItem, SourceHealth, UniverseConfig
from pipeline.market_hours import is_quiet_period
from pipeline.providers.registry import ProviderSet

_SOURCE_RANK = {"edgar": 0, "rss": 1, "news": 2}  # keep the most primary copy


class SourceResult(BaseModel):
    items: list[RawItem]
    quotes: list[Quote]
    health: list[SourceHealth]


def _norm_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    return f"{host}{path}"  # drops scheme, query (utm_*) and fragments


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", title.lower()).strip()


def _is_dupe(a: RawItem, b: RawItem) -> bool:
    if _norm_url(a.url) == _norm_url(b.url):
        return True
    return SequenceMatcher(None, _norm_title(a.title), _norm_title(b.title)).ratio() >= 0.90


def dedupe(items: list[RawItem]) -> list[RawItem]:
    """URL + fuzzy-title dedupe. Keeps the most primary source (EDGAR > RSS >
    news), then the earliest timestamp; merges ticker_guess when missing."""
    ordered = sorted(items, key=lambda i: (_SOURCE_RANK.get(i.source, 9), i.ts, i.id))
    kept: list[RawItem] = []
    for item in ordered:
        match = next((k for k in kept if _is_dupe(k, item)), None)
        if match is None:
            kept.append(item)
        elif match.ticker_guess is None and item.ticker_guess is not None:
            kept[kept.index(match)] = match.model_copy(
                update={"ticker_guess": item.ticker_guess}
            )
    return sorted(kept, key=lambda i: (i.ts, i.id))


def _health(
    provider: str,
    items: list[RawItem],
    error: Exception | None,
    now: datetime,
    stale_after: timedelta,
    quiet: bool,
) -> SourceHealth:
    if error is not None:
        return SourceHealth(
            provider=provider,  # type: ignore[arg-type]
            status="failed",
            last_ts=None,
            detail=f"{type(error).__name__}: {error}",
        )
    last_ts = max((i.ts for i in items), default=None)
    overdue = last_ts is None or now - last_ts > stale_after
    if overdue and quiet:
        # Between sessions a slow feed is expected, not a fault — report it
        # healthy. detail is left for the UI to synthesize ("last pull <t>")
        # except the empty case, which has no timestamp to render.
        detail = (
            None
            if last_ts is not None
            else f"No new {provider.upper()} items — quiet between sessions"
        )
        return SourceHealth(provider=provider, status="ok", last_ts=last_ts, detail=detail)  # type: ignore[arg-type]
    if overdue:
        # Inside the pre-market/session window a quiet feed IS the alarm. detail
        # stays None so the dashboard renders its live "N min old" phrasing.
        return SourceHealth(provider=provider, status="stale", last_ts=last_ts, detail=None)  # type: ignore[arg-type]
    return SourceHealth(provider=provider, status="ok", last_ts=last_ts, detail=None)  # type: ignore[arg-type]


def run_source(universe: UniverseConfig, providers: ProviderSet, now: datetime) -> SourceResult:
    stale_after = timedelta(minutes=universe.thresholds.stale_after_min)
    # Between sessions (weekends/overnight) a quiet tape is expected, so health
    # softens its alarms; the strict pre-market checks apply only when fresh
    # data is actually due.
    quiet = is_quiet_period(now)
    calls = {
        "rss": lambda: providers.rss.fetch(universe.rss_feeds),
        "edgar": lambda: providers.edgar.fetch(universe.tickers),
        "news": lambda: providers.news.search(universe.tickers, universe.sector_keywords),
    }

    raw: dict[str, list[RawItem]] = {}
    errors: dict[str, Exception | None] = {}
    quotes: list[Quote] = []
    quote_error: Exception | None = None

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {name: pool.submit(fn) for name, fn in calls.items()}
        quote_future = pool.submit(providers.quotes.snapshot, universe.tickers)
        for name, fut in futures.items():
            try:
                raw[name], errors[name] = fut.result(), None
            except Exception as exc:
                raw[name], errors[name] = [], exc
        try:
            quotes = quote_future.result()
        except Exception as exc:
            quote_error = exc

    # No look-ahead: a stage must never read data timestamped after "now"
    # (= generated_at). Enforced here so it holds for every downstream stage.
    fresh = [i for items in raw.values() for i in items if i.ts <= now]

    items = dedupe(fresh)
    quotes = [
        q.model_copy(
            update={"rvol": round(q.volume / q.avg_volume, 2) if q.avg_volume else None}
        )
        for q in quotes
    ]

    health = [
        _health(name, [i for i in raw[name] if i.ts <= now], errors[name], now, stale_after, quiet)
        for name in ("rss", "edgar", "news")
    ]
    health.append(_quote_health(quotes, quote_error, now, quiet))
    return SourceResult(items=items, quotes=quotes, health=health)


def _quote_health(
    quotes: list[Quote], error: Exception | None, now: datetime, quiet: bool
) -> SourceHealth:
    """Quote health in the product's voice. The per-vendor reason is surfaced
    (concisely — the noisy prefix stripped) so a headless deploy is debuggable
    from the rail alone. Between sessions there is no pre-market tape to expect,
    so an outage is a soft amber, not a red ✕."""
    if error is not None:
        reason = _quote_failure_reason(error)
        if quiet:
            return SourceHealth(
                provider="quotes",
                status="stale",
                last_ts=None,
                detail=f"No live quotes between sessions — {reason}",
            )
        return SourceHealth(
            provider="quotes",
            status="failed",
            last_ts=None,
            detail=f"Live market data unavailable — {reason}",
        )
    if quotes:
        return SourceHealth(provider="quotes", status="ok", last_ts=now, detail=None)
    if quiet:
        return SourceHealth(
            provider="quotes", status="ok", last_ts=None, detail="No quotes — quiet between sessions"
        )
    return SourceHealth(provider="quotes", status="stale", last_ts=None, detail=None)


def _quote_failure_reason(error: Exception, limit: int = 240) -> str:
    """Concise per-vendor reason for the rail: the chain lists why each vendor
    failed; drop the boilerplate prefix and cap the length."""
    msg = str(error).replace("all quote vendors failed — ", "").strip()
    return msg if len(msg) <= limit else msg[: limit - 1] + "…"
