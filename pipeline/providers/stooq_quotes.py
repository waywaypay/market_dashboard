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

QUOTE_URL = "https://stooq.com/q/l/"
HISTORY_URL = "https://stooq.com/q/d/l/"

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
        transport: httpx.BaseTransport | None = None,
    ):
        self.companies = companies or {}  # ticker -> display name
        self.trailing_days = int(
            trailing_days
            if trailing_days is not None
            else os.environ.get("QUOTES_TRAILING_DAYS", "20")
        )
        self.throttle_s = throttle_s
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
            QUOTE_URL,
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
            HISTORY_URL,
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

    def _get(self, url: str, params: dict[str, str]) -> httpx.Response:
        time.sleep(self.throttle_s)
        response = self._client.get(url, params=params)
        if response.status_code != 200:
            raise RuntimeError(f"Stooq returned HTTP {response.status_code}")
        return response


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
