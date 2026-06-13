"""Market-aware source health.

The pipeline refreshes around the clock, but a quiet news/filing feed and an
absent pre-market tape are only alarming when fresh data is actually due — the
weekday pre-market/session window. Between sessions (overnight, weekends) the
same quiet is normal and must not light the rail up red. These tests pin that
distinction so a Saturday dashboard reads calm without blinding the genuine
pre-market staleness alarm.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pipeline.contracts import RawItem
from pipeline.contracts.universe import load_universe
from pipeline.evals.harness import UNIVERSES_DIR
from pipeline.market_hours import is_quiet_period
from pipeline.providers.registry import build_providers
from pipeline.stages.source import _health, _quote_health, run_source

STALE_AFTER = timedelta(minutes=75)
# 2026-06-10 is a Wednesday; 14:00 UTC = 10:00 ET = inside the regular session.
SESSION_NOW = datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)


def _item(ts: datetime) -> RawItem:
    return RawItem(id="x", source="rss", url="u", title="t", raw_text="r", ts=ts)


# ------------------------------------------------------------- is_quiet_period


@pytest.mark.parametrize(
    "iso, quiet",
    [
        ("2026-06-13T18:00:00+00:00", True),   # Saturday afternoon
        ("2026-06-14T12:00:00+00:00", True),   # Sunday
        ("2026-06-10T14:00:00+00:00", False),  # Wed 10:00 ET — regular session
        ("2026-06-10T09:00:00+00:00", False),  # Wed 05:00 ET — pre-market window
        ("2026-06-10T06:00:00+00:00", True),   # Wed 02:00 ET — overnight, pre-4am
        ("2026-06-10T21:00:00+00:00", True),   # Wed 17:00 ET — after the close
        ("2026-06-12T19:59:00+00:00", False),  # Fri 15:59 ET — last minute of session
        ("2026-06-12T20:00:00+00:00", True),   # Fri 16:00 ET — closed
    ],
)
def test_is_quiet_period(iso: str, quiet: bool) -> None:
    assert is_quiet_period(datetime.fromisoformat(iso)) is quiet


# ---------------------------------------------------------------------- _health


def test_health_fresh_feed_is_ok_regardless_of_session() -> None:
    fresh = [_item(SESSION_NOW - timedelta(minutes=10))]
    for quiet in (True, False):
        h = _health("rss", fresh, None, SESSION_NOW, STALE_AFTER, quiet)
        assert h.status == "ok" and h.detail is None and h.last_ts is not None


def test_health_overdue_feed_is_stale_in_session_but_ok_when_quiet() -> None:
    old = [_item(SESSION_NOW - timedelta(minutes=200))]  # 200 > 75-min threshold

    in_session = _health("rss", old, None, SESSION_NOW, STALE_AFTER, quiet=False)
    assert in_session.status == "stale"
    assert in_session.detail is None  # the UI renders its live "N min old" phrasing
    assert in_session.last_ts is not None

    between = _health("rss", old, None, SESSION_NOW, STALE_AFTER, quiet=True)
    assert between.status == "ok"  # a 3h-old story is normal on a Saturday
    assert between.detail is None and between.last_ts is not None  # -> "last pull <t>"


def test_health_empty_feed_down_in_session_but_explained_when_quiet() -> None:
    in_session = _health("edgar", [], None, SESSION_NOW, STALE_AFTER, quiet=False)
    assert in_session.status == "stale" and in_session.last_ts is None

    between = _health("edgar", [], None, SESSION_NOW, STALE_AFTER, quiet=True)
    assert between.status == "ok" and between.last_ts is None
    assert "quiet between sessions" in (between.detail or "")  # the rail has text to show


def test_health_provider_error_always_fails() -> None:
    err = RuntimeError("boom")
    for quiet in (True, False):
        h = _health("news", [], err, SESSION_NOW, STALE_AFTER, quiet)
        assert h.status == "failed" and "boom" in (h.detail or "")


# ----------------------------------------------------------------- _quote_health


def test_quote_health_vendor_outage_is_red_in_session_soft_when_quiet() -> None:
    err = RuntimeError("all quote vendors failed — YahooQuoteProvider: ...; StooqProvider: 404")

    in_session = _quote_health([], err, SESSION_NOW, quiet=False)
    assert in_session.status == "failed"
    # the raw vendor chain stays in the logs; the rail gets a clean line
    assert "Yahoo" not in (in_session.detail or "") and "404" not in (in_session.detail or "")
    assert "temporarily unavailable" in (in_session.detail or "")

    between = _quote_health([], err, SESSION_NOW, quiet=True)
    assert between.status == "stale"  # no pre-market tape is expected between sessions
    assert "between sessions" in (between.detail or "")


def test_quote_health_ok_when_quotes_present() -> None:
    from pipeline.contracts import Quote

    q = [Quote(ticker="VCYT", name="Veracyte", last=1.0, chg_pct=0.0, volume=1, avg_volume=1, sigma=3.0)]
    h = _quote_health(q, None, SESSION_NOW, quiet=False)
    assert h.status == "ok" and h.last_ts == SESSION_NOW


# --------------------------------------------------------- end-to-end via fixtures


def test_run_source_softens_stale_feeds_between_sessions(monkeypatch) -> None:
    for var in ("BRIEF_PROVIDERS", "BRIEF_RSS", "BRIEF_EDGAR", "BRIEF_NEWS", "BRIEF_QUOTES"):
        monkeypatch.delenv(var, raising=False)  # library default: fixtures
    universe = load_universe(UNIVERSES_DIR / "diagnostics.yaml")
    weekend = datetime(2026, 6, 13, 18, 0, tzinfo=timezone.utc)  # Saturday

    in_session = {
        h.provider: h
        for h in run_source(universe, build_providers(universe, SESSION_NOW), SESSION_NOW).health
    }
    between = {
        h.provider: h
        for h in run_source(universe, build_providers(universe, weekend), weekend).health
    }

    # The diagnostics fixture seeds a deliberately stale EDGAR feed (newest 8-K
    # ~82 min old). That trips the pre-market bar during the session...
    assert in_session["edgar"].status == "stale"
    # ...but the very same gap reads as normal once the market is closed.
    assert between["edgar"].status == "ok"
