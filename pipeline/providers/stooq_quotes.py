"""Keyless fallback quote provider backed by Stooq (https://stooq.com).

Stooq serves real, slightly-delayed market data as plain CSV with no API key
and no aggressive rate limiting — the fallback tier for when Yahoo has a
shared cloud IP in its penalty box. One batched quote CSV prices every
ticker; per-ticker daily-history CSVs (cached per UTC day, bounded to
completed sessions via d2) supply sigma and avg_volume.

Trade-offs vs Yahoo: the last price is the exchange-delayed print (~15 min
during the session) and there is no pre-market tape — until today's first
print appears the provider reports the prior close FLAT rather than
mislabeling yesterday's move as today's.
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


class StooqQuoteProvider(QuoteProvider):
    def __init__(
        self,
        companies: dict[str, str] | None = None,
        trailing_days: int | None = None,
        throttle_s: float = 0.2,
        max_attempts: int = 2,
        backoff_s: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.companies = companies or {}  # ticker -> display name
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
        rows = self._quote_rows(tickers)
        quotes: list[Quote] = []
        for ticker in tickers:
            row = rows.get(ticker.upper())
            if row is None:
                print(f"[quotes] {ticker}: not in Stooq response — skipped", file=sys.stderr)
                continue
            quote = self._build(ticker, row)
            if quote is not None:
                quotes.append(quote)
        if not quotes:
            raise RuntimeError(
                f"Stooq quote pull returned no usable rows for {len(tickers)} tickers"
            )
        return quotes

    # -- internals ------------------------------------------------------------

    def _build(self, ticker: str, row: dict) -> Quote | None:
        last = _num(row.get("Close"))
        quote_date = _parse_date(row.get("Date"))
        if last is None or quote_date is None:
            print(f"[quotes] {ticker}: Stooq row has no price — skipped", file=sys.stderr)
            return None

        closes, volumes = self._history_best_effort(ticker)
        moves = _pct_moves(closes)[-self.trailing_days :]
        trailing_vol = volumes[-self.trailing_days :]

        # Stooq has no pre-market tape: until today's first delayed print,
        # the freshest row is the PRIOR session — report it flat instead of
        # passing off yesterday's move as today's.
        today_us = datetime.now(US_EASTERN).date()
        if quote_date == today_us and closes:
            chg_pct = round((last / closes[-1] - 1.0) * 100, 2)
            volume = int(_num(row.get("Volume")) or 0)
        else:
            chg_pct = 0.0
            volume = int(_num(row.get("Volume")) or 0) if quote_date == today_us else 0

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
        """sigma/avg inputs must never take the price down with them."""
        try:
            return self._daily_history(ticker)
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
        retrying with capped backoff. A single bad edge (404/limit/5xx) or a
        transient transport error must not sink the whole fallback tier."""
        last_response: httpx.Response | None = None
        last_error: Exception | None = None
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
                    return response
                last_response = response  # 404/limit/5xx: the mirror or a retry may answer
        if last_response is not None:
            raise RuntimeError(f"Stooq returned HTTP {last_response.status_code}")
        assert last_error is not None
        raise last_error


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
