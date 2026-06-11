"""Real quote provider backed by Yahoo Finance's public chart API.

Two bounded GETs per ticker against /v8/finance/chart/{symbol} — no API key:

  1. trailing daily history (range=3mo, interval=1d) -> avg_volume and sigma
     (stdev of the trailing <=QUOTES_TRAILING_DAYS daily % moves, default 20),
     plus a fallback previous close;
  2. today's tape (range=1d, interval=5m, includePrePost=true) -> the latest
     traded price including the pre-market session, today's cumulative
     volume, and the official previous close for chg_pct.

rvol and unusual-move flags are derived downstream in the deterministic fuse
stage; this provider only reports what traded. Per-ticker failures are logged
to stderr and skipped — one delisted symbol must not sink the strip — and the
pull raises only when every ticker fails, which the source stage surfaces as
SourceHealth(quotes=failed) with the reason.

The endpoint is public but unofficial (it is what powers finance.yahoo.com):
requests carry a browser-like User-Agent (Yahoo's CDN rejects bot UAs) and
are throttled. If the vendor misbehaves, mix quotes back to fixtures with
BRIEF_QUOTES=fixture — the interface stays vendor-neutral.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import datetime, timezone

import httpx

from pipeline.contracts import Quote
from pipeline.providers.base import QuoteProvider
from pipeline.providers.util import make_client

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

DEFAULT_SIGMA = 3.0  # conservative stand-in when history is too short (recent IPOs)
MIN_RETURNS = 5  # fewer trailing %-moves than this and sigma is not estimable


class YahooQuoteProvider(QuoteProvider):
    def __init__(
        self,
        companies: dict[str, str] | None = None,
        trailing_days: int | None = None,
        throttle_s: float = 0.15,
        transport: httpx.BaseTransport | None = None,
    ):
        self.companies = companies or {}  # ticker -> display name
        self.trailing_days = int(
            trailing_days
            if trailing_days is not None
            else os.environ.get("QUOTES_TRAILING_DAYS", "20")
        )
        self.throttle_s = throttle_s
        self._client = make_client(
            transport=transport,
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
        )

    # -- public interface ---------------------------------------------------

    def snapshot(self, tickers: list[str]) -> list[Quote]:
        quotes: list[Quote] = []
        last_error: Exception | None = None
        for ticker in tickers:
            try:
                quotes.append(self._quote_for(ticker))
            except Exception as exc:
                last_error = exc
                print(f"[quotes] {ticker}: {type(exc).__name__}: {exc}", file=sys.stderr)
        if tickers and not quotes:
            raise RuntimeError(
                f"Yahoo quote pull failed for all {len(tickers)} tickers "
                f"(last error: {last_error}) — check connectivity, or mix quotes "
                "back to fixtures with BRIEF_QUOTES=fixture"
            )
        return quotes

    # -- internals ------------------------------------------------------------

    def _quote_for(self, ticker: str) -> Quote:
        daily = self._chart(ticker, {"range": "3mo", "interval": "1d"})
        live = self._chart(
            ticker, {"range": "1d", "interval": "5m", "includePrePost": "true"}
        )

        closes, volumes = _completed_days(daily)
        moves = _pct_moves(closes)[-self.trailing_days :]
        trailing_vol = volumes[-self.trailing_days :]

        meta = live.get("meta") or {}
        last = _latest_trade(live)
        prev_close = (
            meta.get("chartPreviousClose")
            or meta.get("previousClose")
            or (closes[-1] if closes else None)
        )
        if last is None or not prev_close:
            raise ValueError("no traded price in chart response")

        return Quote(
            ticker=ticker,
            name=self.companies.get(ticker) or meta.get("shortName") or ticker,
            last=round(float(last), 4),
            chg_pct=round((float(last) / float(prev_close) - 1.0) * 100, 2),
            volume=int(meta.get("regularMarketVolume") or _bar_volume(live)),
            avg_volume=int(statistics.mean(trailing_vol)) if trailing_vol else 0,
            sigma=round(statistics.stdev(moves), 2)
            if len(moves) >= MIN_RETURNS
            else DEFAULT_SIGMA,
        )

    def _chart(self, symbol: str, params: dict[str, str]) -> dict:
        time.sleep(self.throttle_s)  # polite pacing — two requests per ticker
        response = self._client.get(CHART_URL.format(symbol=symbol), params=params)
        try:
            chart = response.json().get("chart") or {}
        except ValueError:
            chart = {}
        result = (chart.get("result") or [None])[0]
        if response.status_code != 200 or result is None:
            error = (chart.get("error") or {}).get("description") or (
                f"HTTP {response.status_code}"
            )
            raise RuntimeError(f"Yahoo chart {symbol}: {error}")
        return result


def _bars(result: dict) -> tuple[list[int], list, list]:
    """(timestamps, closes, volumes) — null-padded arrays as Yahoo sends them."""
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    return (
        result.get("timestamp") or [],
        quote.get("close") or [],
        quote.get("volume") or [],
    )


def _completed_days(result: dict) -> tuple[list[float], list[int]]:
    """Daily closes+volumes excluding today's in-progress bar and null padding."""
    today = datetime.now(timezone.utc).date()
    ts, closes, volumes = _bars(result)
    out_closes: list[float] = []
    out_volumes: list[int] = []
    for i, t in enumerate(ts):
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue
        if datetime.fromtimestamp(t, tz=timezone.utc).date() >= today:
            continue  # trailing stats must use completed sessions only
        volume = volumes[i] if i < len(volumes) else None
        out_closes.append(float(close))
        out_volumes.append(int(volume) if volume else 0)
    return out_closes, out_volumes


def _pct_moves(closes: list[float]) -> list[float]:
    return [(b - a) / a * 100.0 for a, b in zip(closes, closes[1:]) if a]


def _latest_trade(result: dict) -> float | None:
    """Last non-null intraday close (pre/post sessions included) = the most
    recent trade; falls back to the meta's regular-market price."""
    _, closes, _ = _bars(result)
    for value in reversed(closes):
        if value is not None:
            return float(value)
    price = (result.get("meta") or {}).get("regularMarketPrice")
    return float(price) if price is not None else None


def _bar_volume(result: dict) -> int:
    _, _, volumes = _bars(result)
    return sum(int(v) for v in volumes if v)
