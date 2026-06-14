"""Keyed quote provider backed by Alpha Vantage GLOBAL_QUOTE.

The last-resort tier: for deploys whose shared egress IP is blocked by the
keyless vendors (Yahoo 429, Stooq 404), a key buys a path that answers from
cloud IPs. Needs ALPHAVANTAGE_API_KEY.

The free tier is strict — 25 requests/day, 5/min — and GLOBAL_QUOTE prices one
symbol per call (no keyless batch endpoint). So results are cached per ticker
for ALPHAVANTAGE_TTL_S (default 12h): comfortably one or two universe refreshes
a day, and plenty for "as of close" between sessions, when the close does not
move anyway. Misses (unknown symbols) are cached too, so a delisted ticker
can't burn the daily budget on every refresh. Rate-limit / over-quota notices
(returned HTTP 200 with a "Note"/"Information" body) abort the batch rather
than hammering a spent key.

GLOBAL_QUOTE carries last price, previous close, the day's change and volume,
and the latest trading day — enough for the strip. It has no trailing history,
so sigma falls back to DEFAULT_SIGMA and avg_volume/RVOL are left unset; the
price is the point. Like the Stooq tier, the change is shown for a real
session (today's print, or the last close while the market is shut) but stays
flat during a weekday pre-market, never passing off a prior session's move as
today's.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from pipeline.contracts import Quote
from pipeline.market_hours import is_quiet_period
from pipeline.providers.base import QuoteProvider
from pipeline.providers.util import make_client

QUOTE_URL = "https://www.alphavantage.co/query"
DEFAULT_SIGMA = 3.0  # GLOBAL_QUOTE has no history; ship a conservative stand-in
US_EASTERN = ZoneInfo("America/New_York")

# ticker -> (monotonic fetch time, Quote | None). None is a cached miss. Shared
# across the per-run provider instances the registry builds, so the strict
# daily budget survives refreshes within a process.
_QUOTE_CACHE: dict[str, tuple[float, Quote | None]] = {}


class _RateLimited(RuntimeError):
    """Alpha Vantage's frequency/quota notice — stop the batch, spare the key."""


class _NoData(RuntimeError):
    """Empty/unknown symbol — skip this ticker, cache the miss."""


class AlphaVantageQuoteProvider(QuoteProvider):
    def __init__(
        self,
        companies: dict[str, str] | None = None,
        api_key: str | None = None,
        throttle_s: float = 1.0,
        ttl_s: float | None = None,
        now: datetime | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.companies = companies or {}
        self.api_key = api_key if api_key is not None else os.environ.get("ALPHAVANTAGE_API_KEY")
        self.throttle_s = throttle_s  # 5 req/min free-tier ceiling -> pace requests
        self.ttl_s = float(
            ttl_s if ttl_s is not None else os.environ.get("ALPHAVANTAGE_TTL_S", "43200")
        )
        self.now = now  # injectable clock; the as-of-close move is time-of-day aware
        self._client = make_client(transport=transport, timeout=10.0)

    def snapshot(self, tickers: list[str]) -> list[Quote]:
        if not tickers:
            return []
        if not self.api_key:
            raise RuntimeError(
                "ALPHAVANTAGE_API_KEY not set — set it (or remove Alpha Vantage from the chain)"
            )
        now = self.now or datetime.now(US_EASTERN)
        today = now.astimezone(US_EASTERN).date()
        quiet = is_quiet_period(now)

        quotes: list[Quote] = []
        last_error: Exception | None = None
        for ticker in tickers:
            try:
                quote = self._quote_for(ticker, today, quiet)
            except _RateLimited as exc:
                # The key is spent / throttled — every further call fails too.
                last_error = exc
                print(f"[quotes] Alpha Vantage rate-limited — stopping: {exc}", file=sys.stderr)
                break
            except _NoData as exc:
                print(f"[quotes] {ticker}: Alpha Vantage — {exc}", file=sys.stderr)
                continue
            except Exception as exc:
                last_error = exc
                print(f"[quotes] {ticker}: Alpha Vantage {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            if quote is not None:
                quotes.append(quote)
        if tickers and not quotes:
            raise RuntimeError(
                f"Alpha Vantage returned no usable quotes for {len(tickers)} tickers "
                f"(last error: {last_error})"
            )
        return quotes

    # -- internals ------------------------------------------------------------

    def _quote_for(self, ticker: str, today: date, quiet: bool) -> Quote | None:
        cached = _QUOTE_CACHE.get(ticker)
        if cached is not None and (time.monotonic() - cached[0]) < self.ttl_s:
            if cached[1] is None:
                raise _NoData("no data (cached miss)")
            return cached[1]
        try:
            quote = self._build(ticker, self._global_quote(ticker), today, quiet)
        except _NoData:
            _QUOTE_CACHE[ticker] = (time.monotonic(), None)  # don't re-spend on a dead symbol
            raise
        _QUOTE_CACHE[ticker] = (time.monotonic(), quote)
        return quote

    def _global_quote(self, ticker: str) -> dict:
        time.sleep(self.throttle_s)
        response = self._client.get(
            QUOTE_URL,
            params={"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": self.api_key},
        )
        if response.status_code != 200:
            raise RuntimeError(f"Alpha Vantage HTTP {response.status_code}")
        data = response.json()
        if "Note" in data or "Information" in data:  # frequency/quota notice (still 200)
            raise _RateLimited(str(data.get("Note") or data.get("Information"))[:160])
        row = data.get("Global Quote") or {}
        if not row:
            raise _NoData("empty Global Quote (unknown symbol)")
        return row

    def _build(self, ticker: str, row: dict, today: date, quiet: bool) -> Quote:
        last = _num(row.get("05. price"))
        prev = _num(row.get("08. previous close"))
        if last is None:
            raise _NoData("no price in Global Quote")
        latest_day = _parse_date(row.get("07. latest trading day"))

        # Show the session's move + volume when the data is today's print, or the
        # last close while the market is shut ("as of close"). During a weekday
        # pre-market, stay flat — never pass off a prior session's move as today's.
        show_session = latest_day == today or quiet
        chg_pct = round((last / prev - 1.0) * 100, 2) if (prev and show_session) else 0.0
        volume = int(_num(row.get("06. volume")) or 0) if show_session else 0

        return Quote(
            ticker=ticker,
            name=self.companies.get(ticker) or ticker,
            last=round(last, 4),
            chg_pct=chg_pct,
            volume=volume,
            avg_volume=0,  # GLOBAL_QUOTE has no trailing average
            sigma=DEFAULT_SIGMA,  # ...nor history for a real sigma
        )


def _num(value: str | None) -> float | None:
    if value is None or str(value).strip() in ("", "None", "N/A"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
