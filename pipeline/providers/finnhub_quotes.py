"""Keyed quote provider backed by Finnhub (https://finnhub.io).

A keyed tier that answers from cloud IPs the keyless vendors are blocked on.
The free tier is a generous 60 calls/minute, so per-ticker ``/quote`` calls
comfortably price a peer universe each refresh. ``/quote`` carries the current
price, previous close and the day's change — but no volume or trailing history,
so avg_volume/RVOL are left unset and ``sigma`` falls back to DEFAULT_SIGMA; the
price is the point. A per-ticker failure is skipped, not fatal; an over-rate
notice stops the batch rather than hammering a throttled key.

Like the other off-tape tiers, the change is shown for a real session (today's
print, or the last close while the market is shut) and stays flat during a
weekday pre-market, never passing off a prior session's move as today's.

Set any of: FINNHUB_KEY, FINHUB_KEY, FINNHUB_API_KEY (matched by normalized
name, so spelling/separators don't matter).
"""

from __future__ import annotations

import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from pipeline.contracts import Quote
from pipeline.market_hours import is_quiet_period
from pipeline.providers.base import QuoteProvider
from pipeline.providers.util import make_client, match_api_key

QUOTE_URL = "https://finnhub.io/api/v1/quote"
DEFAULT_SIGMA = 3.0  # /quote has no trailing history for a real sigma
US_EASTERN = ZoneInfo("America/New_York")

_KEY_NAMES = {
    "FINNHUBKEY",
    "FINHUBKEY",
    "FINNHUBAPIKEY",
    "FINHUBAPIKEY",
    "FINNHUBTOKEN",
    "FINHUBTOKEN",
}


def api_key_from_env() -> str | None:
    return match_api_key(_KEY_NAMES)


class _RateLimited(RuntimeError):
    """Finnhub's 60/min ceiling — stop the batch, spare the key."""


class FinnhubQuoteProvider(QuoteProvider):
    def __init__(
        self,
        companies: dict[str, str] | None = None,
        api_key: str | None = None,
        throttle_s: float = 0.0,  # 60/min covers a normal peer universe unthrottled
        now: datetime | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.companies = companies or {}
        self.api_key = api_key if api_key is not None else api_key_from_env()
        self.throttle_s = throttle_s
        self.now = now  # injectable clock; the as-of-close move is time-of-day aware
        self._client = make_client(transport=transport, timeout=10.0)

    def snapshot(self, tickers: list[str]) -> list[Quote]:
        if not tickers:
            return []
        if not self.api_key:
            raise RuntimeError("Finnhub API key not set (FINNHUB_KEY)")
        now = self.now or datetime.now(US_EASTERN)
        today = now.astimezone(US_EASTERN).date()
        quiet = is_quiet_period(now)

        quotes: list[Quote] = []
        last_error: Exception | None = None
        for ticker in tickers:
            try:
                if self.throttle_s:
                    time.sleep(self.throttle_s)
                quote = self._quote_for(ticker, today, quiet)
            except _RateLimited as exc:
                last_error = exc
                print(f"[quotes] Finnhub rate-limited — stopping: {exc}", file=sys.stderr)
                break
            except Exception as exc:
                last_error = exc
                print(f"[quotes] {ticker}: Finnhub {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            if quote is not None:
                quotes.append(quote)
        if tickers and not quotes:
            raise RuntimeError(
                f"Finnhub returned no usable quotes for {len(tickers)} tickers "
                f"(last error: {last_error})"
            )
        return quotes

    def _quote_for(self, ticker: str, today: date, quiet: bool) -> Quote | None:
        response = self._client.get(
            QUOTE_URL, params={"symbol": ticker, "token": self.api_key}
        )
        if response.status_code == 429:
            raise _RateLimited("HTTP 429 — 60 calls/min exceeded")
        if response.status_code in (401, 403):
            raise RuntimeError(f"Finnhub rejected the key (HTTP {response.status_code})")
        if response.status_code != 200:
            raise RuntimeError(f"Finnhub HTTP {response.status_code}")
        data = response.json()

        last = _num(data.get("c"))  # current price
        prev = _num(data.get("pc"))  # previous close
        if not last:  # unknown/invalid symbol comes back all-zeros
            return None
        latest_day = _epoch_to_date(data.get("t"))

        show_session = latest_day == today or quiet
        chg_pct = round((last / prev - 1.0) * 100, 2) if (prev and show_session) else 0.0
        return Quote(
            ticker=ticker,
            name=self.companies.get(ticker) or ticker,
            last=round(last, 4),
            chg_pct=chg_pct,
            volume=0,  # /quote carries no volume
            avg_volume=0,  # ...nor a trailing average -> RVOL unset
            sigma=DEFAULT_SIGMA,
        )


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _epoch_to_date(value: object) -> date | None:
    try:
        return datetime.fromtimestamp(int(value), tz=US_EASTERN).date()  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError, OSError):
        return None
