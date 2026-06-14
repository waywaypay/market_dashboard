"""Historical-series provider: fixture determinism + FMP wire formats (mocked).

Presentation-only and best-effort — a failure must yield {} and never raise, so
the overlay chart degrades to an empty state instead of taking the run down.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from pipeline.providers.history import fetch_history

NOW = datetime(2026, 6, 13, 12, 0, tzinfo=ZoneInfo("America/New_York"))  # Saturday
COMPANIES = {"VCYT": "Veracyte", "NTRA": "Natera"}


@pytest.fixture(autouse=True)
def _no_ambient_fmp_key(monkeypatch):
    for name in ("FMP_KEY", "FMP_API_KEY", "FINANCIALMODELINGPREP_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_fixture_history_is_deterministic_and_ascending() -> None:
    a = fetch_history(COMPANIES, ["VCYT", "NTRA"], NOW, "fixture")
    b = fetch_history(COMPANIES, ["VCYT", "NTRA"], NOW, "fixture")
    assert set(a) == {"VCYT", "NTRA"}
    assert a == b  # reproducible
    pts = a["VCYT"]
    assert len(pts) >= 50  # ~3 months of weekdays
    assert [p.d for p in pts] == sorted(p.d for p in pts)  # ascending by date
    assert pts[-1].d < "2026-06-13"  # ends before "today", no look-ahead


def test_real_history_uses_fmp_v3_ascending(monkeypatch) -> None:
    monkeypatch.setenv("FMP_KEY", "k")

    def handler(req: httpx.Request) -> httpx.Response:
        assert "financialmodelingprep.com" in req.url.host
        assert req.url.path.startswith("/api/v3/historical-price-full/")
        sym = req.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={  # FMP returns newest-first
                "symbol": sym,
                "historical": [
                    {"date": "2026-06-12", "close": 47.3},
                    {"date": "2026-06-11", "close": 46.0},
                ],
            },
        )

    out = fetch_history(
        COMPANIES, ["VCYT"], NOW, "real", transport=httpx.MockTransport(handler)
    )
    assert [(p.d, p.c) for p in out["VCYT"]] == [("2026-06-11", 46.0), ("2026-06-12", 47.3)]


def test_real_history_falls_back_to_stable(monkeypatch) -> None:
    monkeypatch.setenv("FMP_KEY", "k")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.startswith("/api/v3/"):  # not on this key
            return httpx.Response(403, json={"Error Message": "Exclusive Endpoint"})
        assert req.url.path == "/stable/historical-price-eod/light"
        return httpx.Response(
            200,
            json=[
                {"symbol": "VCYT", "date": "2026-06-12", "price": 47.3},
                {"symbol": "VCYT", "date": "2026-06-11", "price": 46.0},
            ],
        )

    out = fetch_history(
        COMPANIES, ["VCYT"], NOW, "real", transport=httpx.MockTransport(handler)
    )
    assert [p.c for p in out["VCYT"]] == [46.0, 47.3]


def test_real_history_is_empty_without_a_key() -> None:
    assert fetch_history(COMPANIES, ["VCYT"], NOW, "real") == {}


def test_history_never_raises_on_a_dead_source(monkeypatch) -> None:
    monkeypatch.setenv("FMP_KEY", "k")
    transport = httpx.MockTransport(lambda req: httpx.Response(500))
    assert fetch_history(COMPANIES, ["VCYT"], NOW, "real", transport=transport) == {}
