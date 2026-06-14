"""Real quote provider backed by Yahoo Finance.

One path — the v8 chart API. Yahoo locked the batched v7 quote endpoint
behind a cookie+crumb handshake that now 406s the crumb and 401s the quote
("User is unable to access this feature") for datacenter IPs, so it is gone;
v8 chart is the endpoint that still answers without auth. Per ticker we pull
two charts: daily history (3mo/1d) for sigma and average volume, and today's
tape (1d/5m with pre/post bars) for the last traded price and the % move off
the prior close. History is cached per UTC day and is best-effort — when it
can't be fetched the conservative DEFAULT_SIGMA ships and avg_volume falls to
zero (rvol simply goes unknown) instead of failing the ticker, so price data
never dies with history.

Shared cloud egress IPs get rate-limited aggressively, so every request is
paced (throttle_s), 429/5xx get a capped exponential backoff honoring
Retry-After, and a 429 never triggers an immediate mirror-host hit (the
budget is per source IP — that only digs the hole deeper). If Yahoo still
misbehaves, mix quotes back to fixtures with BRIEF_QUOTES=fixture — the
interface stays vendor-neutral.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import date, datetime, timezone

import httpx

from pipeline.contracts import Quote
from pipeline.providers.base import QuoteProvider
from pipeline.providers.util import make_client

# query1 and query2 serve the same data from different edges; failing over
# keeps one bad edge from sinking the whole pull (429s skip the mirror).
CHART_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

DEFAULT_SIGMA = 3.0  # conservative stand-in when history is unavailable/too short
MIN_RETURNS = 5  # fewer trailing %-moves than this and sigma is not estimable
RETRY_AFTER_CAP_S = 10.0  # never let a Retry-After header stall the boot refresh

# ticker -> (utc date, closes, volumes). Daily history is immutable within a
# session, so refreshes after the first each day skip the per-ticker calls.
# Module-level so the per-run provider instances the registry builds share it.
_HISTORY_CACHE: dict[str, tuple[date, list[float], list[int]]] = {}


class YahooQuoteProvider(QuoteProvider):
    def __init__(
        self,
        companies: dict[str, str] | None = None,
        trailing_days: int | None = None,
        throttle_s: float = 0.5,
        max_attempts: int = 3,
        backoff_s: float = 1.5,
        deadline_s: float | None = None,
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
        # Hard budget for one snapshot. Without it, a blackholing or hard-
        # throttling vendor times out per request x retries x tickers and a
        # refresh can run for many minutes.
        self.deadline_s = float(
            deadline_s
            if deadline_s is not None
            else os.environ.get("QUOTES_DEADLINE_S", "120")
        )
        self._deadline = float("inf")
        self._client = make_client(
            transport=transport,
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            timeout=8.0,  # Yahoo answers fast or not at all
        )

    # -- public interface ---------------------------------------------------

    def snapshot(self, tickers: list[str]) -> list[Quote]:
        """Price every ticker via per-ticker v8 chart requests. Each ticker is
        independent: one failing never sinks the rest, and the run only raises
        when nothing at all priced (so source.py can flag the source failed)."""
        if not tickers:
            return []
        self._deadline = time.monotonic() + self.deadline_s
        quotes: list[Quote] = []
        last_error: Exception | None = None
        for n, ticker in enumerate(tickers):
            if n and time.monotonic() > self._deadline:
                print(
                    f"[quotes] time budget exhausted — pulled {len(quotes)} of "
                    f"{len(tickers)} tickers",
                    file=sys.stderr,
                )
                break
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

    # -- per-ticker chart pull ------------------------------------------------

    def _quote_for(self, ticker: str) -> Quote:
        closes, volumes = self._history_best_effort(ticker)
        live = self._chart(
            ticker, {"range": "1d", "interval": "5m", "includePrePost": "true"}
        )

        moves = _pct_moves(closes)[-self.trailing_days :]
        trailing_vol = volumes[-self.trailing_days :]

        meta = live.get("meta") or {}
        last = _latest_bar_close(live)
        if last is not None:
            prev_close = (
                meta.get("chartPreviousClose")
                or meta.get("previousClose")
                or (closes[-1] if closes else None)
            )
        else:
            # Nothing has traded yet today (thin name pre-open, or a bar
            # outage): report the prior close, flat. Falling through to the
            # usual prev_close would mislabel yesterday's move as today's.
            last = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
            prev_close = last
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

    # -- shared plumbing --------------------------------------------------------

    def _history_best_effort(self, ticker: str) -> tuple[list[float], list[int]]:
        """sigma/avg inputs must never take the price down with them."""
        cached = _HISTORY_CACHE.get(ticker)
        if cached is None and time.monotonic() > self._deadline:
            return [], []  # out of budget — DEFAULT_SIGMA beats a late brief
        try:
            return self._daily_history(ticker)
        except Exception as exc:
            print(
                f"[quotes] {ticker}: history unavailable "
                f"({type(exc).__name__}: {exc}) — using default sigma",
                file=sys.stderr,
            )
            return [], []

    def _daily_history(self, ticker: str) -> tuple[list[float], list[int]]:
        today = datetime.now(timezone.utc).date()
        cached = _HISTORY_CACHE.get(ticker)
        if cached is not None and cached[0] == today:
            return cached[1], cached[2]
        daily = self._chart(ticker, {"range": "3mo", "interval": "1d"})
        closes, volumes = _completed_days(daily)
        if closes:
            _HISTORY_CACHE[ticker] = (today, closes, volumes)
        return closes, volumes

    def _chart(self, symbol: str, params: dict[str, str]) -> dict:
        response = self._request(
            f"https://{{host}}/v8/finance/chart/{symbol}", params
        )
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

    def _request(self, url_template: str, params: dict[str, str]) -> httpx.Response:
        """GET with pacing, mirror failover and capped backoff on 429/5xx.
        Returns the last response once nothing retryable is left; raises only
        when every attempt died in transport (DNS/connect/timeout)."""
        last_error: Exception | None = None
        last_response: httpx.Response | None = None
        wait = self.backoff_s
        for attempt in range(self.max_attempts):
            if attempt:
                if time.monotonic() + wait > self._deadline:
                    break  # retrying would blow the budget — surface what we have
                time.sleep(wait)
                wait *= 2
            for host in CHART_HOSTS:
                time.sleep(self.throttle_s)  # polite pacing under Yahoo's budget
                try:
                    response = self._client.get(
                        url_template.format(host=host), params=params
                    )
                except httpx.HTTPError as exc:  # DNS/connect/timeout -> try the mirror
                    last_error = exc
                    continue
                last_response = response
                if response.status_code == 429:
                    # rate-limited: hitting the mirror now only digs the hole
                    # deeper (the budget is per source IP) — back off instead
                    wait = max(wait, _retry_after_s(response))
                    break
                if response.status_code >= 500:
                    continue  # the mirror may be healthy
                return response
        if last_response is not None:
            return last_response
        assert last_error is not None
        raise last_error


def _retry_after_s(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After")
    try:
        return min(float(value), RETRY_AFTER_CAP_S) if value else 0.0
    except ValueError:
        return 0.0


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


def _latest_bar_close(result: dict) -> float | None:
    """Last non-null intraday close (pre/post sessions included) = the most
    recent trade today; None when nothing has printed yet."""
    _, closes, _ = _bars(result)
    for value in reversed(closes):
        if value is not None:
            return float(value)
    return None


def _bar_volume(result: dict) -> int:
    _, _, volumes = _bars(result)
    return sum(int(v) for v in volumes if v)
