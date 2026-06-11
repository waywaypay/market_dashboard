"""Real news-search provider backed by Exa (https://exa.ai).

Two bounded semantic searches per run against POST /search with the news
category and a published-date window: one over the universe's company names
(subject + peers + private watch), one over its sector keywords. Results map
to RawItems; the source stage dedupes overlap with RSS/EDGAR.

Requires EXA_API_KEY. The provider constructs without it (so registry wiring
never crashes) and fails loudly at search time — surfacing in the dashboard
as SourceHealth(news=failed) with the reason.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from pipeline.contracts import RawItem
from pipeline.providers.base import NewsProvider
from pipeline.providers.util import infer_ticker, make_client

EXA_SEARCH_URL = "https://api.exa.ai/search"


class ExaNewsProvider(NewsProvider):
    def __init__(
        self,
        companies: dict[str, str],
        watch: list[str] | None = None,
        api_key: str | None = None,
        num_results: int | None = None,
        lookback_hours: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.companies = companies  # ticker -> display name
        self.watch = watch or []
        self.api_key = api_key or os.environ.get("EXA_API_KEY")
        self.num_results = min(
            int(num_results or os.environ.get("EXA_NUM_RESULTS", "10")), 25
        )
        self.lookback = timedelta(
            hours=lookback_hours
            if lookback_hours is not None
            else float(os.environ.get("EXA_LOOKBACK_HOURS", "36"))
        )
        self._client = make_client(transport=transport)

    def search(self, tickers: list[str], sector_keywords: list[str]) -> list[RawItem]:
        if not self.api_key:
            raise RuntimeError(
                "EXA_API_KEY is not set — get a key at https://exa.ai and export it, "
                "or run the news source on fixtures (BRIEF_NEWS=fixture)"
            )
        start = (datetime.now(timezone.utc) - self.lookback).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        names = [self.companies.get(t, t) for t in tickers] + list(self.watch)
        queries = [
            "Market-moving company news (deals, regulators, earnings, products) about: "
            + ", ".join(names[:14])
        ]
        if sector_keywords:
            queries.append(
                "Latest industry news on: " + ", ".join(sector_keywords[:10])
            )

        items: dict[str, RawItem] = {}
        for query in queries:
            for item in self._search_once(query, start):
                items.setdefault(item.id, item)  # overlap across queries is fine
        return list(items.values())

    def _search_once(self, query: str, start_published: str) -> list[RawItem]:
        response = self._client.post(
            EXA_SEARCH_URL,
            headers={"x-api-key": self.api_key or ""},
            json={
                "query": query,
                "type": "auto",
                "category": "news",
                "numResults": self.num_results,
                "startPublishedDate": start_published,
                "contents": {"text": {"maxCharacters": 1200}},
            },
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Exa search returned HTTP {response.status_code}: {response.text[:200]}"
            )

        out: list[RawItem] = []
        for result in response.json().get("results", []):
            url = result.get("url")
            title = (result.get("title") or "").strip()
            ts = _parse_published(result.get("publishedDate"))
            if not url or not title or ts is None:
                continue  # undated results can't pass the no-look-ahead bar
            text = (result.get("text") or "").strip() or title
            out.append(
                RawItem(
                    id="exa-" + hashlib.sha1(url.encode()).hexdigest()[:12],
                    source="news",
                    feed=_domain(url),
                    url=url,
                    title=title,
                    raw_text=text,
                    ts=ts,
                    ticker_guess=infer_ticker(f"{title} {text}", self.companies),
                )
            )
        return out


def _parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _domain(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.") or "exa"
