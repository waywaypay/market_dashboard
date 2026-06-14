"""Historical daily closes for the price-overlay chart.

Best-effort and presentation-only: the chart is a bonus, never a gate, so this
never raises — an unreachable source just yields ``{}`` and the chart shows an
empty state. It mirrors the quote tiering: a deterministic fixture series in
demo mode, and FMP in real mode (the one historical source that answers from
the cloud IPs the keyless vendors are blocked on). FMP serves history under
``/api/v3`` (legacy keys) or ``/stable`` (newer keys); we try v3 then stable,
exactly like the quote provider, and parse tolerantly.
"""

from __future__ import annotations

import math
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from pipeline.contracts import PricePoint
from pipeline.providers.fmp_quotes import api_key_from_env as fmp_api_key
from pipeline.providers.util import make_client

US_EASTERN = ZoneInfo("America/New_York")
DEFAULT_LOOKBACK_DAYS = 90  # ~3 months (the chart window)

V3_HIST_URL = "https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}"
STABLE_HIST_URL = "https://financialmodelingprep.com/stable/historical-price-eod/light"


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
    try:
        if mode == "fixture":
            return {t: _fixture_series(t, now, lookback_days) for t in tickers}
        key = fmp_api_key()
        if not key:
            # real mode, but no keyed historical source reachable from this host
            return {}
        return _fmp_history(tickers, now, lookback_days, key, transport)
    except Exception as exc:  # presentation-only — a chart must never fail the run
        print(f"[history] unavailable ({type(exc).__name__}: {exc})", file=sys.stderr)
        return {}


# -- real: FMP historical (v3 -> stable) --------------------------------------


def _fmp_history(
    tickers: list[str],
    now: datetime,
    lookback_days: int,
    api_key: str,
    transport: httpx.BaseTransport | None,
) -> dict[str, list[PricePoint]]:
    start = (now.astimezone(US_EASTERN).date() - timedelta(days=lookback_days)).isoformat()
    client = make_client(transport=transport, timeout=10.0)

    def one(ticker: str) -> tuple[str, list[PricePoint]]:
        try:
            return ticker, _fmp_one(client, ticker, start, api_key)
        except Exception as exc:
            print(f"[history] {ticker}: FMP {type(exc).__name__}: {exc}", file=sys.stderr)
            return ticker, []

    out: dict[str, list[PricePoint]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for ticker, series in pool.map(one, tickers):
            if series:
                out[ticker] = series
    return out


def _fmp_one(client: httpx.Client, ticker: str, start: str, api_key: str) -> list[PricePoint]:
    try:
        return _fmp_v3(client, ticker, start, api_key)
    except Exception:
        return _fmp_stable(client, ticker, start, api_key)


def _fmp_v3(client: httpx.Client, ticker: str, start: str, api_key: str) -> list[PricePoint]:
    data = _json(
        client,
        V3_HIST_URL.format(symbol=ticker.upper()),
        {"from": start, "serietype": "line", "apikey": api_key},
    )
    rows = data.get("historical") if isinstance(data, dict) else None
    if not rows:
        raise RuntimeError("no historical array")
    return _points(rows, close_keys=("close", "adjClose"))


def _fmp_stable(client: httpx.Client, ticker: str, start: str, api_key: str) -> list[PricePoint]:
    data = _json(
        client, STABLE_HIST_URL, {"symbol": ticker.upper(), "from": start, "apikey": api_key}
    )
    if not isinstance(data, list) or not data:
        raise RuntimeError("no historical rows")
    return _points(data, close_keys=("price", "close", "adjClose"))


def _json(client: httpx.Client, url: str, params: dict[str, str]):
    response = client.get(url, params=params)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    data = response.json()
    if isinstance(data, dict) and ("Error Message" in data or "message" in data):
        raise RuntimeError(str(data.get("Error Message") or data.get("message"))[:120])
    return data


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
    pts.sort(key=lambda p: p.d)  # FMP returns newest-first; the chart wants ascending
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
