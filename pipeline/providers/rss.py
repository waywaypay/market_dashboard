"""Real RSS provider: fetches and parses the universe's configured feeds.

Feeds are configured in the universe YAML as {label, url}; label-only entries
(paywalled publications with no public feed) are skipped here and remain
available to the fixture provider. Per-feed failures are tolerated — one dead
feed must not blank the brief — but if every feed fails the fetch raises so
the source stage reports SourceHealth(failed) honestly.
"""

from __future__ import annotations

import calendar
import hashlib
import sys
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from pipeline.contracts import RawItem
from pipeline.contracts.universe import RSSFeed
from pipeline.providers.base import RSSProvider
from pipeline.providers.util import infer_ticker, make_client, strip_tags


class HttpRSSProvider(RSSProvider):
    def __init__(
        self,
        companies: dict[str, str] | None = None,
        lookback_hours: float = 36.0,
        max_per_feed: int = 25,
        transport: httpx.BaseTransport | None = None,
    ):
        self.companies = companies or {}
        self.lookback = timedelta(hours=lookback_hours)
        self.max_per_feed = max_per_feed
        self._client = make_client(transport=transport)

    def fetch(self, feeds: list[RSSFeed]) -> list[RawItem]:
        pullable = [f for f in feeds if f.url]
        if not pullable:
            raise RuntimeError(
                "no feeds with URLs configured — add `url:` to rss_feeds entries "
                "in the universe YAML (label-only feeds are fixture-only)"
            )
        cutoff = datetime.now(timezone.utc) - self.lookback
        items: list[RawItem] = []
        errors: list[str] = []
        for feed in pullable:
            try:
                items.extend(self._fetch_feed(feed, cutoff))
            except Exception as exc:
                errors.append(f"{feed.label}: {type(exc).__name__}: {exc}")
        if errors:
            print(f"[rss] {len(errors)} feed(s) failed: {'; '.join(errors)}", file=sys.stderr)
        if not items and errors:
            raise RuntimeError(f"all {len(errors)} feed pulls failed ({errors[0]} …)")
        return items

    def _fetch_feed(self, feed: RSSFeed, cutoff: datetime) -> list[RawItem]:
        assert feed.url is not None
        response = self._client.get(feed.url)
        response.raise_for_status()
        parsed = feedparser.parse(response.content)

        out: list[RawItem] = []
        for entry in parsed.entries[: self.max_per_feed * 2]:
            link = entry.get("link")
            title = (entry.get("title") or "").strip()
            if not link or not title:
                continue
            ts = _entry_ts(entry)
            if ts is None or ts < cutoff:
                continue  # undated or stale entries are not pre-market signal
            summary = strip_tags(entry.get("summary") or entry.get("description") or "", 1500)
            out.append(
                RawItem(
                    id="rss-" + hashlib.sha1(link.encode()).hexdigest()[:12],
                    source="rss",
                    feed=feed.label,
                    url=link,
                    title=title,
                    raw_text=summary or title,
                    ts=ts,
                    ticker_guess=infer_ticker(f"{title} {summary}", self.companies),
                )
            )
            if len(out) >= self.max_per_feed:
                break
        return out


def _entry_ts(entry: feedparser.FeedParserDict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st is not None:
            return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
    return None
