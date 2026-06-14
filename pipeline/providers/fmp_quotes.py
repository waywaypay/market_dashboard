"""Keyed quote provider backed by Financial Modeling Prep (FMP).

A keyed tier that answers from cloud IPs the keyless vendors (Yahoo 429, Stooq
bot-wall) are blocked on. One batched ``/quote`` call prices the whole universe
— price, previous close, day volume AND average volume — so, unlike the other
keyed tiers, it yields RVOL, not just a price. The free tier is 250 requests/
day; a batched refresh is a single call, comfortably within budget even across
cold starts. There is no trailing daily history in the quote, so ``sigma``
falls back to DEFAULT_SIGMA (RVOL and the move carry the strip).

Like the other off-tape tiers, the change is shown for a real session (today's
print, or the last close while the market is shut) but stays flat during a
weekday pre-market, never passing off a prior session's move as today's.

Set any of: FMP_KEY, FMP_API_KEY, FINANCIALMODELINGPREP_API_KEY (matched by
normalized name, so spelling/separators don't matter).
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from pipeline.contracts import Quote
from pipeline.market_hours import is_quiet_period
from pipeline.providers.base import QuoteProvider
from pipeline.providers.util import make_client, match_api_key

# FMP serves the same quote under two APIs: legacy keys use /api/v3 (and can
# batch many symbols in one call); keys issued on the newer plans use /stable
# (one symbol per call). We can't know which a given key is provisioned for, so
# we try the batched v3 first and fall back to per-symbol /stable — whichever
# answers. Field names drift slightly between them, hence tolerant parsing.
V3_BATCH_URL = "https://financialmodelingprep.com/api/v3/quote/{symbols}"
STABLE_QUOTE_URL = "https://financialmodelingprep.com/stable/quote"
DEFAULT_SIGMA = 3.0  # the quote has no trailing history for a real sigma
US_EASTERN = ZoneInfo("America/New_York")

_KEY_NAMES = {
    "FMPKEY",
    "FMPAPIKEY",
    "FINANCIALMODELINGPREPKEY",
    "FINANCIALMODELINGPREPAPIKEY",
}


def api_key_from_env() -> str | None:
    return match_api_key(_KEY_NAMES)


class FmpQuoteProvider(QuoteProvider):
    def __init__(
        self,
        companies: dict[str, str] | None = None,
        api_key: str | None = None,
        now: datetime | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.companies = companies or {}
        self.api_key = api_key if api_key is not None else api_key_from_env()
        self.now = now  # injectable clock; the as-of-close move is time-of-day aware
        self._client = make_client(transport=transport, timeout=10.0)

    def snapshot(self, tickers: list[str]) -> list[Quote]:
        if not tickers:
            return []
        if not self.api_key:
            raise RuntimeError("FMP API key not set (FMP_KEY)")
        now = self.now or datetime.now(US_EASTERN)
        today = now.astimezone(US_EASTERN).date()
        quiet = is_quiet_period(now)

        rows = self._quote_rows(tickers)
        quotes: list[Quote] = []
        for ticker in tickers:
            row = rows.get(ticker.upper())
            if row is None:
                print(f"[quotes] {ticker}: FMP — not in response, skipped", file=sys.stderr)
                continue
            quote = self._build(ticker, row, today, quiet)
            if quote is not None:
                quotes.append(quote)
        if not quotes:
            raise RuntimeError(f"FMP returned no usable quotes for {len(tickers)} tickers")
        return quotes

    def _quote_rows(self, tickers: list[str]) -> dict[str, dict]:
        """Try the batched v3 endpoint (one call); if the key isn't provisioned
        for it, fall back to per-symbol /stable. Whichever serves wins."""
        try:
            return self._v3_batch(tickers)
        except Exception as exc:
            print(
                f"[quotes] FMP v3 batch unavailable ({type(exc).__name__}: {exc}) "
                "— retrying on /stable per symbol",
                file=sys.stderr,
            )
            return self._stable_per_symbol(tickers)

    def _v3_batch(self, tickers: list[str]) -> dict[str, dict]:
        symbols = ",".join(t.upper() for t in tickers)
        data = self._get_json(V3_BATCH_URL.format(symbols=symbols), {"apikey": self.api_key})
        rows = _index_by_symbol(data)
        if not rows:
            raise RuntimeError("v3 batch returned no rows")
        return rows

    def _stable_per_symbol(self, tickers: list[str]) -> dict[str, dict]:
        rows: dict[str, dict] = {}
        last_error: Exception | None = None
        for ticker in tickers:
            try:
                data = self._get_json(
                    STABLE_QUOTE_URL, {"symbol": ticker.upper(), "apikey": self.api_key}
                )
                rows.update(_index_by_symbol(data))
            except Exception as exc:
                last_error = exc
                print(f"[quotes] {ticker}: FMP /stable {type(exc).__name__}: {exc}", file=sys.stderr)
        if not rows:
            raise RuntimeError(f"FMP /stable returned nothing (last error: {last_error})")
        return rows

    def _get_json(self, url: str, params: dict[str, str]) -> list:
        response = self._client.get(url, params=params)
        if response.status_code == 429:  # daily/minute budget spent
            raise RuntimeError("rate-limited (HTTP 429)")
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:120]}")
        data = response.json()
        # Success is a JSON array; errors (bad key, plan/endpoint limit) come back
        # as an object with an "Error Message" — surface it rather than reading {}.
        if isinstance(data, dict):
            msg = data.get("Error Message") or data.get("message") or str(data)[:160]
            raise RuntimeError(f"FMP rejected the request: {msg}")
        return data

    def _build(self, ticker: str, row: dict, today: date, quiet: bool) -> Quote | None:
        last = _num(row.get("price"))
        prev = _num(row.get("previousClose"))
        if last is None:
            return None
        latest_day = _epoch_to_date(row.get("timestamp"))

        # Show the session's move + volume for today's print, or the last close
        # while the market is shut; during a weekday pre-market stay flat.
        show_session = latest_day == today or quiet
        chg_pct = round((last / prev - 1.0) * 100, 2) if (prev and show_session) else 0.0
        volume = int(_num(row.get("volume")) or 0) if show_session else 0

        # v3 calls it avgVolume; /stable calls it averageVolume — accept either,
        # so RVOL works regardless of which endpoint served the key.
        avg_volume = _num(row.get("avgVolume")) or _num(row.get("averageVolume")) or 0

        return Quote(
            ticker=ticker,
            name=self.companies.get(ticker) or row.get("name") or ticker,
            last=round(last, 4),
            chg_pct=chg_pct,
            volume=volume,
            avg_volume=int(avg_volume),  # -> RVOL in the fuse stage
            sigma=DEFAULT_SIGMA,
        )


def _index_by_symbol(data: list) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for row in data:
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            rows[symbol] = row
    return rows


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
