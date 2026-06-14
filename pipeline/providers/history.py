"""Historical daily closes for the price-overlay chart.

Best-effort and presentation-only: the chart is a bonus, never a gate, so this
never raises — an unreachable source just yields ``{}`` and the chart shows an
empty state. It mirrors the quote tiering: a deterministic fixture series in
demo mode, and in real mode a chain of keyed sources that answer from the cloud
IPs the keyless vendors are blocked on:

  1. FMP historical (``/api/v3`` legacy keys, ``/stable`` newer keys)
  2. Alpha Vantage ``TIME_SERIES_DAILY`` (free, fills any ticker FMP couldn't —
     FMP's free plan does not always include historical EOD)

Per ticker, the first source with data wins; the rest of the chain backfills
only what's still missing, so one source's plan limits don't blank the chart.
"""

from __future__ import annotations

import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from pipeline.contracts import PricePoint
from pipeline.providers.alphavantage_quotes import api_key_from_env as av_api_key
from pipeline.providers.fmp_quotes import api_key_from_env as fmp_api_key
from pipeline.providers.util import make_client

US_EASTERN = ZoneInfo("America/New_York")
DEFAULT_LOOKBACK_DAYS = 90  # ~3 months (the chart window)

V3_HIST_URL = "https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}"
STABLE_HIST_URL = "https://financialmodelingprep.com/stable/historical-price-eod/light"
AV_URL = "https://www.alphavantage.co/query"


def fetch_history(
    companies: dict[str, str],
    tickers: list[str],
    now: datetime,
    mode: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, list[PricePoint]]:
    """Per-ticker daily closes, ascending by date. Never raises."""
    if not tickers:
        return {}
    if mode == "fixture":
        return {t: _fixture_series(t, now, lookback_days) for t in tickers}

    start = (now.astimezone(US_EASTERN).date() - timedelta(days=lookback_days)).isoformat()
    client = make_client(transport=transport, timeout=10.0)
    out: dict[str, list[PricePoint]] = {}

    # 1) FMP (the user's primary historical key)
    fmp_key = fmp_api_key()
    if fmp_key:
        _collect(out, tickers, lambda t: _fmp_one(client, t, start, fmp_key))

    # 2) Alpha Vantage backfills whatever FMP couldn't return
    missing = [t for t in tickers if t not in out]
    av_key = av_api_key()
    if missing and av_key:
        # AV's free tier is rate-limited (5/min, 25/day) — pace and run serially
        _collect(out, missing, lambda t: _av_one(client, t, start, av_key), serial=True)

    have = len(out)
    if have < len(tickers):
        print(
            f"[history] {have}/{len(tickers)} tickers have a series "
            f"(fmp_key={'y' if fmp_key else 'n'}, av_key={'y' if av_key else 'n'})",
            file=sys.stderr,
        )
    return out


def _collect(out, tickers, fetch_one, serial: bool = False) -> None:
    """Run fetch_one per ticker (best-effort), recording non-empty series."""

    def one(ticker: str) -> tuple[str, list[PricePoint]]:
        try:
            return ticker, fetch_one(ticker)
        except Exception as exc:
            print(f"[history] {ticker}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return ticker, []

    if serial:
        results = [one(t) for t in tickers]
    else:
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(one, tickers))
    for ticker, series in results:
        if series:
            out[ticker] = series


# -- FMP historical (v3 -> stable) --------------------------------------------


def _fmp_one(client: httpx.Client, ticker: str, start: str, api_key: str) -> list[PricePoint]:
    try:
        data = _json(
            client,
            V3_HIST_URL.format(symbol=ticker.upper()),
            {"from": start, "serietype": "line", "apikey": api_key},
        )
        return _points(_rows(data), ("close", "adjClose", "price"))
    except Exception:
        data = _json(
            client, STABLE_HIST_URL, {"symbol": ticker.upper(), "from": start, "apikey": api_key}
        )
        return _points(_rows(data), ("price", "close", "adjClose"))


# -- Alpha Vantage TIME_SERIES_DAILY (free) -----------------------------------


def _av_one(client: httpx.Client, ticker: str, start: str, api_key: str) -> list[PricePoint]:
    time.sleep(0.2)  # gentle pacing under AV's free 5/min ceiling
    response = client.get(
        AV_URL,
        params={
            "function": "TIME_SERIES_DAILY",
            "symbol": ticker.upper(),
            "outputsize": "compact",  # last 100 sessions covers the ~3mo window
            "apikey": api_key,
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"AV HTTP {response.status_code}")
    data = response.json()
    if "Note" in data or "Information" in data or "Error Message" in data:
        raise RuntimeError(str(data.get("Note") or data.get("Information") or data.get("Error Message"))[:120])
    rows = [
        {"date": day, "close": vals.get("4. close")}
        for day, vals in (data.get("Time Series (Daily)") or {}).items()
        if day >= start
    ]
    return _points(rows, ("close",))


# -- shared parsing -----------------------------------------------------------


def _json(client: httpx.Client, url: str, params: dict[str, str]):
    response = client.get(url, params=params)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    data = response.json()
    if isinstance(data, dict) and ("Error Message" in data or "message" in data):
        raise RuntimeError(str(data.get("Error Message") or data.get("message"))[:120])
    return data


def _rows(data) -> list:
    """FMP returns either a bare list or {"historical": [...]} depending on the
    endpoint/plan — accept both."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("historical") or data.get("results") or []
    return []


def _points(rows: list, close_keys: tuple[str, ...]) -> list[PricePoint]:
    pts: list[PricePoint] = []
    for row in rows:
        day = row.get("date")
        close = next((row[k] for k in close_keys if row.get(k) is not None), None)
        if not day or close is None:
            continue
        try:
            pts.append(PricePoint(d=str(day)[:10], c=round(float(close), 4)))
        except (TypeError, ValueError):
            continue
    pts.sort(key=lambda p: p.d)  # sources vary newest/oldest-first; the chart wants ascending
    return pts


# -- fixture: a deterministic series so the demo + tests have a chart ---------


def _fixture_series(ticker: str, now: datetime, lookback_days: int) -> list[PricePoint]:
    """A stable, plausible per-ticker walk (weekdays only, ending yesterday).
    Deterministic from the ticker so the demo and tests are reproducible."""
    seed = sum(ord(ch) for ch in ticker)
    sessions = max(2, round(lookback_days * 5 / 7))
    end = now.astimezone(US_EASTERN).date() - timedelta(days=1)

    days: list = []
    cursor = end
    while len(days) < sessions:
        if cursor.weekday() < 5:  # Mon–Fri
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()

    price = 50.0 + (seed % 200)  # arbitrary but stable starting level
    pts: list[PricePoint] = []
    for i, day in enumerate(days):
        step = math.sin((i + seed) * 0.3) * 0.9 + ((seed % 5) - 2) * 0.05  # % move
        price = max(1.0, price * (1 + step / 100))
        pts.append(PricePoint(d=day.isoformat(), c=round(price, 4)))
    return pts
