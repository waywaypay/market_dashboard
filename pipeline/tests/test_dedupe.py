"""Source-stage dedupe: URL normalization + fuzzy-title matching."""

from __future__ import annotations

from datetime import datetime, timezone

from pipeline.contracts import RawItem
from pipeline.stages.source import dedupe

NOW = datetime(2026, 6, 10, 13, 0, tzinfo=timezone.utc)


def _item(id, url, title, source="rss", ticker=None, mins=-30):
    return RawItem(
        id=id,
        source=source,
        feed=source,
        url=url,
        title=title,
        raw_text=title,
        ts=NOW.replace(minute=0) if mins == 0 else NOW,
        ticker_guess=ticker,
    )


def test_dedupe_by_normalized_url() -> None:
    a = _item("a", "https://www.example.com/story/", "Alpha launches", source="rss")
    b = _item("b", "http://example.com/story?utm_source=x", "ALPHA launches!", source="news")
    out = dedupe([a, b])
    assert len(out) == 1


def test_dedupe_keeps_most_primary_source() -> None:
    rss = _item("r", "https://x.com/a", "Company beats on revenue", source="rss")
    edgar = _item("e", "https://y.com/b", "Company beats on revenue", source="edgar", ticker="ABC")
    out = dedupe([rss, edgar])
    assert len(out) == 1
    assert out[0].source == "edgar"  # EDGAR outranks RSS
    assert out[0].ticker_guess == "ABC"


def test_dedupe_merges_missing_ticker() -> None:
    primary = _item("e", "https://y.com/b", "Big news today", source="edgar", ticker=None)
    secondary = _item("n", "https://z.com/c", "Big news today", source="news", ticker="XYZ")
    out = dedupe([primary, secondary])
    assert len(out) == 1
    assert out[0].ticker_guess == "XYZ"  # recovered from the dupe


def test_distinct_stories_survive() -> None:
    a = _item("a", "https://x.com/1", "Alpha gets FDA clearance")
    b = _item("b", "https://x.com/2", "Beta misses on guidance")
    out = dedupe([a, b])
    assert len(out) == 2


def test_dedupe_is_deterministic() -> None:
    items = [
        _item("a", "https://x.com/1", "Same headline here"),
        _item("b", "https://x.com/2", "Same headline here"),
        _item("c", "https://x.com/3", "Totally different"),
    ]
    out1 = dedupe(items)
    out2 = dedupe(list(reversed(items)))
    assert [i.title for i in out1] == [i.title for i in out2]
