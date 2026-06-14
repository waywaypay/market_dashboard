"""Keyless fallback quote provider backed by Stooq (https://stooq.com).

Stooq serves real, slightly-delayed market data as plain CSV with no API key
and no aggressive rate limiting — the fallback tier for when Yahoo has a
shared cloud IP in its penalty box. Per-ticker daily-history CSVs (cached per
UTC day, bounded to completed sessions via d2) supply sigma and avg_volume,
and — crucially — the last completed close.

Two endpoints, two roles. The daily-history endpoint (/q/d/l/) is the
workhorse and is reachable even from egress IPs that the light-quote endpoint
(/q/l/) 404s. So the live tape is treated as a *bonus*: when it answers we
show the delayed intraday print; when it doesn't, every ticker still prices
off its last daily close. The market is never left empty just because the
live tape is down.

Some egress IPs (datacenter ranges in particular) get neither CSV nor an
honest error: Stooq answers HTTP 200 with a JavaScript "verify your browser"
interstitial, or a plain-text "Exceeded the daily hits limit" notice. That is
not data — parsing it yields an empty quote and a misleading "nothing
reachable" rail. Such a body is detected, never parsed, and (being per-IP and
identical across mirrors, retries and tickers) fails the tier *fast* with the
real reason, so the chain falls through to the next vendor instead of walling
every ticker.

Trade-offs vs Yahoo: the last price is the exchange-delayed print (~15 min
during the session) and there is no pre-market tape. With the market shut
(weekends, holidays, overnight) — or whenever only daily history is
reachable — the quote is reported FLAT at the last completed close ("as of
close"), never passing off a prior session's move as today's.
"""

from __future__ import annotations

import csv
import io
import os
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from pipeline.contracts import Quote
from pipeline.market_hours import is_quiet_period
from pipeline.providers.base import QuoteProvider
from pipeline.providers.util import make_client

# stooq.pl is the origin; stooq.com the international mirror. They serve the
# same keyless CSV endpoints, and a shared cloud egress IP that one edge has
# 404'd/limited the other sometimes still answers — so we fail over between
# them exactly like the Yahoo provider does across its query1/query2 edges.
HOSTS = ("stooq.com", "stooq.pl")
QUOTE_PATH = "/q/l/"
HISTORY_PATH = "/q/d/l/"

DEFAULT_SIGMA = 3.0  # conservative stand-in when history is unavailable/too short
MIN_RETURNS = 5
HISTORY_DAYS = 120  # calendar lookback; ~80 trading sessions

US_EASTERN = ZoneInfo("America/New_York")  # .us symbols trade on US sessions

# ticker -> (utc date, closes, volumes); shared across per-run instances.
_HISTORY_CACHE: dict[str, tuple[date, list[float], list[int]]] = {}


def _symbol(ticker: str) -> str:
    return ticker.lower().replace(".", "-") + ".us"


class _Blocked(RuntimeError):
    """Stooq served a bot-challenge / hit-limit page instead of CSV. Per-IP and
    terminal for this run, so retrying mirrors or probing more tickers is futile
    — surface it and let the chain move to the next vendor."""


class StooqQuoteProvider(QuoteProvider):
    def __init__(
        self,
        companies: dict[str, str] | None = None,
        trailing_days: int | None = None,
        throttle_s: float = 0.2,
        max_attempts: int = 2,
        backoff_s: float = 1.0,
        now: datetime | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.companies = companies or {}  # ticker -> display name
        self.now = now  # injectable clock; the as-of-close move is time-of-day aware
        self.trailing_days = int(
            trailing_days
            if trailing_days is not None
            else os.environ.get("QUOTES_TRAILING_DAYS", "20")
        )
        self.throttle_s = throttle_s
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self._client = make_client(transport=transport, timeout=10.0)

    # -- public interface ---------------------------------------------------

    def snapshot(self, tickers: list[str]) -> list[Quote]:
        if not tickers:
            return []
        now = self.now or datetime.now(US_EASTERN)
        today_us = now.astimezone(US_EASTERN).date()
        quiet = is_quiet_period(now)
        rows = self._quote_rows_best_effort(tickers)
        quotes: list[Quote] = []
        for ticker in tickers:
            try:
                quote = self._build(ticker, rows.get(ticker.upper()), today_us, quiet)
            except _Blocked as exc:
                # A challenge/limit page dooms every ticker identically — stop on
                # the first rather than re-hitting the wall N times, and report
                # the real reason so the rail isn't a misleading "nothing
                # reachable". The chain then falls through to the next vendor.
                raise RuntimeError(f"Stooq unavailable from this host — {exc}") from exc
            if quote is not None:
                quotes.append(quote)
        if not quotes:
            raise RuntimeError(
                f"Stooq returned no usable rows for {len(tickers)} tickers "
                "(neither the live tape nor daily history was reachable)"
            )
        return quotes

    # -- internals ------------------------------------------------------------

    def _build(self, ticker: str, row: dict | None, today_us: date, quiet: bool) -> Quote | None:
        """Build a quote for one ticker. `row` is the live-tape print when the
        light endpoint answered, else None — in which case we price off the last
        completed daily close, so the market is never blank."""
        closes, volumes = self._history_best_effort(ticker)
        prior_close = closes[-1] if closes else None

        row_last = _num(row.get("Close")) if row is not None else None
        quote_date = _parse_date(row.get("Date")) if row is not None else None

        if row_last is not None and quote_date == today_us:
            # A live (delayed) tick today: show its move vs the last completed
            # close, with the day's volume.
            last: float | None = row_last
            volume = int(_num(row.get("Volume")) or 0)
            chg_pct = round((row_last / prior_close - 1.0) * 100, 2) if prior_close else 0.0
        else:
            # No print for today — quote "as of close". Prefer the light tape's
            # last price, else the last completed daily close.
            last = row_last if row_last is not None else prior_close
            volume = 0
            # Market shut (weekend/holiday/overnight) -> show that session's
            # actual move, the convention every finance UI uses at close. Inside
            # a trading day still awaiting the first print, stay flat rather than
            # passing off the prior session's move as today's.
            if last is not None and quiet and len(closes) >= 2 and closes[-2]:
                chg_pct = round((closes[-1] / closes[-2] - 1.0) * 100, 2)
            else:
                chg_pct = 0.0

        if last is None:
            print(f"[quotes] {ticker}: no Stooq price or history — skipped", file=sys.stderr)
            return None

        moves = _pct_moves(closes)[-self.trailing_days :]
        trailing_vol = volumes[-self.trailing_days :]
        return Quote(
            ticker=ticker,
            name=self.companies.get(ticker) or ticker,
            last=round(last, 4),
            chg_pct=chg_pct,
            volume=volume,
            avg_volume=int(statistics.mean(trailing_vol)) if trailing_vol else 0,
            sigma=round(statistics.stdev(moves), 2)
            if len(moves) >= MIN_RETURNS
            else DEFAULT_SIGMA,
        )

    def _quote_rows_best_effort(self, tickers: list[str]) -> dict[str, dict]:
        """The live tape is a bonus, not a requirement: when /q/l/ is down (it
        404s from some egress IPs) we still price off the daily close, so a
        light-quote failure degrades to as-of-close, never to an empty strip."""
        try:
            return self._quote_rows(tickers)
        except Exception as exc:
            print(
                f"[quotes] Stooq live tape unavailable ({type(exc).__name__}: {exc}) "
                "— pricing off the last daily close",
                file=sys.stderr,
            )
            return {}

    def _quote_rows(self, tickers: list[str]) -> dict[str, dict]:
        response = self._get(
            QUOTE_PATH,
            {"s": "+".join(_symbol(t) for t in tickers), "f": "sd2t2ohlcv", "h": "", "e": "csv"},
        )
        rows: dict[str, dict] = {}
        for row in csv.DictReader(io.StringIO(response.text)):
            symbol = (row.get("Symbol") or "").upper().removesuffix(".US")
            if symbol:
                rows[symbol.replace("-", ".")] = row
        return rows

    def _history_best_effort(self, ticker: str) -> tuple[list[float], list[int]]:
        """sigma/avg inputs must never take the price down with them — except a
        blocked host, which dooms every ticker and must short-circuit the tier
        rather than silently degrading each one to a default sigma."""
        try:
            return self._daily_history(ticker)
        except _Blocked:
            raise
        except Exception as exc:
            print(
                f"[quotes] {ticker}: Stooq history unavailable "
                f"({type(exc).__name__}: {exc}) — using default sigma",
                file=sys.stderr,
            )
            return [], []

    def _daily_history(self, ticker: str) -> tuple[list[float], list[int]]:
        today = datetime.now(US_EASTERN).date()
        cached = _HISTORY_CACHE.get(ticker)
        if cached is not None and cached[0] == today:
            return cached[1], cached[2]
        response = self._get(
            HISTORY_PATH,
            {
                "s": _symbol(ticker),
                "i": "d",
                # d2 = yesterday: completed sessions only, no partial-bar trims
                "d1": (today - timedelta(days=HISTORY_DAYS)).strftime("%Y%m%d"),
                "d2": (today - timedelta(days=1)).strftime("%Y%m%d"),
            },
        )
        closes: list[float] = []
        volumes: list[int] = []
        for row in csv.DictReader(io.StringIO(response.text)):
            close = _num(row.get("Close"))
            if close is None:
                continue
            closes.append(close)
            volumes.append(int(_num(row.get("Volume")) or 0))
        if closes:
            _HISTORY_CACHE[ticker] = (today, closes, volumes)
        return closes, volumes

    def _get(self, path: str, params: dict[str, str]) -> httpx.Response:
        """GET a Stooq CSV endpoint, failing over across mirror hosts and
        retrying with capped backoff. A genuine CSV reply wins. A challenge /
        hit-limit page (HTTP 200, but HTML or the 'exceeded the daily hits limit'
        sentinel) is per-IP and identical across edges and retries, so after one
        pass over the mirrors it raises _Blocked — never retried, never parsed.
        A single bad edge (404/limit/5xx) or transient transport error still
        fails over and retries; only those must not sink the whole tier."""
        last_response: httpx.Response | None = None
        last_error: Exception | None = None
        block_reason: str | None = None
        wait = self.backoff_s
        for attempt in range(self.max_attempts):
            if attempt:
                time.sleep(wait)
                wait *= 2
            for host in HOSTS:
                time.sleep(self.throttle_s)  # polite pacing — Stooq is generous but not infinite
                try:
                    response = self._client.get(f"https://{host}{path}", params=params)
                except httpx.HTTPError as exc:  # DNS/connect/timeout -> try the mirror
                    last_error = exc
                    continue
                if response.status_code == 200:
                    if _looks_like_csv(response.text):
                        return response
                    # 200 but a wall: note it and try the mirror once — the other
                    # edge is occasionally healthy — but don't retry past that.
                    block_reason = _block_reason(response.text)
                    continue
                last_response = response  # 404/limit/5xx: the mirror or a retry may answer
            if block_reason is not None:
                raise _Blocked(block_reason)
        if last_response is not None:
            raise RuntimeError(f"Stooq returned HTTP {last_response.status_code}")
        assert last_error is not None
        raise last_error


def _looks_like_csv(text: str) -> bool:
    """True only for a genuine Stooq CSV reply, whose first line is a
    comma-separated header (or data) row. Rejects the HTML 'verify your browser'
    interstitial and the plain-text 'Exceeded the daily hits limit' notice that
    some egress IPs receive with HTTP 200, so a blocked edge fails over / fails
    fast instead of being parsed into an empty quote."""
    stripped = text.lstrip()
    if not stripped or stripped[0] == "<":  # HTML challenge / error page
        return False
    first_line = stripped.splitlines()[0]
    if "exceeded the daily hits limit" in first_line.lower():
        return False
    return "," in first_line


def _block_reason(text: str) -> str:
    """A concise, honest reason for the rail when Stooq returns a non-CSV wall."""
    head = text.lstrip()[:300].lower()
    if "exceeded the daily hits limit" in head:
        return "Stooq returned a daily-hit-limit notice, not CSV"
    if "requires javascript" in head or "verify your browser" in head:
        return "Stooq served a JavaScript bot-challenge page, not CSV"
    if head[:1] == "<":
        return "Stooq served an HTML page, not CSV"
    return "Stooq returned a non-CSV body"


def _num(value: str | None) -> float | None:
    if value is None or value.strip() in ("", "N/D", "N/A"):
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


def _pct_moves(closes: list[float]) -> list[float]:
    return [(b - a) / a * 100.0 for a, b in zip(closes, closes[1:]) if a]
