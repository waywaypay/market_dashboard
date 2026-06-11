"""Real quote provider backed by Yahoo Finance.

Primary path — ONE batched request per refresh: the v7 quote API returns
last/pre/post prices, previous close, day volume and 3-month average volume
for every ticker at once. v7 requires Yahoo's cookie+crumb handshake, done
once and cached for the process. sigma (trailing stdev of daily % moves)
still needs per-ticker daily history from the v8 chart API; history is
cached per UTC day and is best-effort — when it can't be fetched the
conservative DEFAULT_SIGMA ships instead of failing the ticker, so price
data never dies with history.

Fallback path — if the handshake or the batched quote fails, per-ticker v8
chart requests (daily history + today's tape with pre/post bars) rebuild
the same Quote.

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
QUOTE_URL = "https://{host}/v7/finance/quote"
CRUMB_URL = "https://{host}/v1/test/getcrumb"
COOKIE_URL = "https://fc.yahoo.com/"

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

DEFAULT_SIGMA = 3.0  # conservative stand-in when history is unavailable/too short
MIN_RETURNS = 5  # fewer trailing %-moves than this and sigma is not estimable
RETRY_AFTER_CAP_S = 10.0  # never let a Retry-After header stall the boot refresh

QUOTE_FIELDS = (
    "symbol,shortName,marketState,preMarketPrice,postMarketPrice,"
    "regularMarketPrice,regularMarketPreviousClose,regularMarketVolume,"
    "averageDailyVolume3Month,averageDailyVolume10Day"
)

# ticker -> (utc date, closes, volumes). Daily history is immutable within a
# session, so refreshes after the first each day skip the per-ticker calls.
# Module-level so the per-run provider instances the registry builds share it.
_HISTORY_CACHE: dict[str, tuple[date, list[float], list[int]]] = {}

# The crumb is tied to Yahoo's session cookie; both survive across the
# per-run provider instances so the handshake happens once per process.
_SESSION: dict = {"crumb": None, "cookies": None}


class YahooQuoteProvider(QuoteProvider):
    def __init__(
        self,
        companies: dict[str, str] | None = None,
        trailing_days: int | None = None,
        throttle_s: float = 0.5,
        max_attempts: int = 3,
        backoff_s: float = 1.5,
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
        self._client = make_client(
            transport=transport,
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
        )

    # -- public interface ---------------------------------------------------

    def snapshot(self, tickers: list[str]) -> list[Quote]:
        if not tickers:
            return []
        try:
            return self._snapshot_batched(tickers)
        except Exception as exc:
            print(
                f"[quotes] batched pull failed ({type(exc).__name__}: {exc}) — "
                "falling back to per-ticker charts",
                file=sys.stderr,
            )
        return self._snapshot_charted(tickers)

    # -- primary: one batched v7 quote call ----------------------------------

    def _snapshot_batched(self, tickers: list[str]) -> list[Quote]:
        rows = self._v7_rows(tickers)
        quotes: list[Quote] = []
        for ticker in tickers:
            row = rows.get(ticker.upper())
            if row is None:
                print(f"[quotes] {ticker}: not in batched response — skipped", file=sys.stderr)
                continue
            quote = self._quote_from_row(ticker, row)
            if quote is not None:
                quotes.append(quote)
        if not quotes:
            raise RuntimeError("batched quote returned no usable rows")
        return quotes

    def _quote_from_row(self, ticker: str, row: dict) -> Quote | None:
        prev = row.get("regularMarketPreviousClose")
        state = str(row.get("marketState") or "").upper()
        if state.startswith("PRE") and row.get("preMarketPrice"):
            last = row["preMarketPrice"]
        elif state.startswith("POST") and row.get("postMarketPrice"):
            last = row["postMarketPrice"]
        else:
            last = row.get("regularMarketPrice")
        if not last or not prev:
            return None

        closes, volumes = self._history_best_effort(ticker)
        moves = _pct_moves(closes)[-self.trailing_days :]
        trailing_vol = volumes[-self.trailing_days :]
        avg_volume = int(
            row.get("averageDailyVolume3Month")
            or row.get("averageDailyVolume10Day")
            or (statistics.mean(trailing_vol) if trailing_vol else 0)
        )
        return Quote(
            ticker=ticker,
            name=self.companies.get(ticker) or row.get("shortName") or ticker,
            last=round(float(last), 4),
            chg_pct=round((float(last) / float(prev) - 1.0) * 100, 2),
            volume=int(row.get("regularMarketVolume") or 0),
            avg_volume=avg_volume,
            sigma=round(statistics.stdev(moves), 2)
            if len(moves) >= MIN_RETURNS
            else DEFAULT_SIGMA,
        )

    def _v7_rows(self, tickers: list[str]) -> dict[str, dict]:
        params = {
            "symbols": ",".join(t.upper() for t in tickers),
            "fields": QUOTE_FIELDS,
            "crumb": self._ensure_crumb(),
        }
        response = self._request(QUOTE_URL, params)
        if response.status_code in (401, 403):  # crumb went stale — redo once
            _SESSION.update(crumb=None, cookies=None)
            params["crumb"] = self._ensure_crumb()
            response = self._request(QUOTE_URL, params)
        if response.status_code != 200:
            raise RuntimeError(
                f"Yahoo quote HTTP {response.status_code}: {response.text[:120]}"
            )
        rows = (response.json().get("quoteResponse") or {}).get("result") or []
        return {str(r.get("symbol") or "").upper(): r for r in rows}

    def _ensure_crumb(self) -> str:
        if _SESSION["crumb"]:
            if _SESSION["cookies"]:
                self._client.cookies.update(_SESSION["cookies"])
            return _SESSION["crumb"]
        try:
            self._client.get(COOKIE_URL)  # any status — we only need the cookie jar
        except httpx.HTTPError:
            pass  # the crumb endpoint may still answer
        response = self._request(CRUMB_URL, {})
        crumb = (response.text or "").strip()
        if response.status_code != 200 or not crumb or "<" in crumb:
            raise RuntimeError(f"Yahoo crumb handshake failed (HTTP {response.status_code})")
        _SESSION["crumb"] = crumb
        _SESSION["cookies"] = dict(self._client.cookies)
        return crumb

    # -- fallback: per-ticker chart requests ----------------------------------

    def _snapshot_charted(self, tickers: list[str]) -> list[Quote]:
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
