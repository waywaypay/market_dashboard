"""Real-provider tests against mocked HTTP transports (httpx.MockTransport).

These pin the wire formats each integration depends on — SEC submissions API
shapes, RSS/Atom parsing, the Exa /search request/response — without touching
the network, so they run in CI exactly like everything else.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from pipeline.providers.edgar import SecEdgarProvider
from pipeline.providers.exa_news import ExaNewsProvider
from pipeline.providers.rss import HttpRSSProvider
from pipeline.providers.util import infer_ticker, strip_tags
from pipeline.contracts.universe import RSSFeed

NOW = datetime.now(timezone.utc)
COMPANIES = {"VCYT": "Veracyte", "NTRA": "Natera", "GH": "Guardant Health"}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# --------------------------------------------------------------------------- util


def test_infer_ticker_prefers_full_name_then_ticker() -> None:
    assert infer_ticker("Guardant Health discloses a rate cut", COMPANIES) == "GH"
    assert infer_ticker("Shares of NTRA jumped pre-market", COMPANIES) == "NTRA"
    assert infer_ticker("A story about something else entirely", COMPANIES) is None
    # case-insensitive names, but tickers must match exact case ("gh" ≠ GH)
    assert infer_ticker("veracyte wins coverage", COMPANIES) == "VCYT"
    assert infer_ticker("the gh patient cohort", COMPANIES) is None


def test_strip_tags_removes_scripts_and_truncates() -> None:
    html = "<html><script>var x=1;</script><body><p>Hello &amp; welcome</p></body></html>"
    assert strip_tags(html) == "Hello & welcome"
    assert strip_tags("<p>" + "word " * 1000 + "</p>", max_chars=50).endswith("…")


# ---------------------------------------------------------------------------- RSS

RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test Feed</title>
<item>
  <title>Natera expands Signatera coverage</title>
  <link>https://example.com/natera-coverage</link>
  <description><![CDATA[<p>Natera said coverage <b>expanded</b> today.</p>]]></description>
  <pubDate>{fresh}</pubDate>
</item>
<item>
  <title>Old story from last month</title>
  <link>https://example.com/old</link>
  <pubDate>{stale}</pubDate>
</item>
<item>
  <title>Undated story</title>
  <link>https://example.com/undated</link>
</item>
</channel></rss>"""


def test_rss_provider_parses_filters_and_infers() -> None:
    fresh = (NOW - timedelta(hours=2)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    stale = (NOW - timedelta(days=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    xml = RSS_XML.format(fresh=fresh, stale=stale).encode()

    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=xml))
    provider = HttpRSSProvider(companies=COMPANIES, transport=transport)
    items = provider.fetch([RSSFeed(label="Test Feed", url="https://example.com/rss")])

    assert len(items) == 1  # stale + undated entries dropped
    item = items[0]
    assert item.source == "rss" and item.feed == "Test Feed"
    assert item.ticker_guess == "NTRA"
    assert "<" not in item.raw_text  # html stripped
    assert item.ts.tzinfo is not None


def test_rss_provider_skips_label_only_and_survives_partial_failure() -> None:
    fresh = (NOW - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    xml = RSS_XML.format(fresh=fresh, stale=fresh).encode()

    def handler(req: httpx.Request) -> httpx.Response:
        if "good" in str(req.url):
            return httpx.Response(200, content=xml)
        return httpx.Response(500)

    provider = HttpRSSProvider(companies=COMPANIES, transport=httpx.MockTransport(handler))
    items = provider.fetch(
        [
            RSSFeed(label="Paywalled"),  # no url -> skipped
            RSSFeed(label="Good", url="https://example.com/good"),
            RSSFeed(label="Down", url="https://example.com/down"),
        ]
    )
    assert {i.feed for i in items} == {"Good"}


def test_rss_provider_raises_when_everything_fails() -> None:
    provider = HttpRSSProvider(
        transport=httpx.MockTransport(lambda req: httpx.Response(503))
    )
    with pytest.raises(RuntimeError):
        provider.fetch([RSSFeed(label="Down", url="https://example.com/down")])
    with pytest.raises(RuntimeError):  # nothing pullable at all
        provider.fetch([RSSFeed(label="Paywalled")])


# -------------------------------------------------------------------------- EDGAR

ACCESSION = "0001384101-26-000045"
ACCESSION_FLAT = ACCESSION.replace("-", "")


def edgar_handler(req: httpx.Request) -> httpx.Response:
    url = str(req.url)
    if url.endswith("company_tickers.json"):
        return httpx.Response(
            200,
            json={
                "0": {"cik_str": 1384101, "ticker": "VCYT", "title": "Veracyte, Inc."},
                "1": {"cik_str": 1604821, "ticker": "NTRA", "title": "Natera, Inc."},
            },
        )
    if "submissions/CIK0001384101" in url:
        return httpx.Response(
            200,
            json={
                "name": "Veracyte, Inc.",
                "filings": {
                    "recent": {
                        "form": ["8-K", "10-Q", "8-K"],
                        "acceptanceDateTime": [
                            _iso(NOW - timedelta(hours=3)),
                            _iso(NOW - timedelta(days=20)),
                            _iso(NOW - timedelta(days=40)),
                        ],
                        "filingDate": ["2026-06-10", "2026-05-21", "2026-05-01"],
                        "accessionNumber": [ACCESSION, "x", "y"],
                        "primaryDocument": ["vcyt-8k.htm", "q.htm", "old8k.htm"],
                        "items": ["2.02,9.01", "", "8.01"],
                    }
                },
            },
        )
    if "submissions/CIK0001604821" in url:  # NTRA: nothing recent
        return httpx.Response(
            200,
            json={"name": "Natera, Inc.", "filings": {"recent": {"form": []}}},
        )
    if url.endswith(f"{ACCESSION_FLAT}/index.json"):
        return httpx.Response(
            200,
            json={
                "directory": {
                    "item": [{"name": "vcyt-8k.htm"}, {"name": "ex99_1.htm"}]
                }
            },
        )
    if url.endswith("ex99_1.htm"):
        return httpx.Response(
            200, text="<html><body><h1>Veracyte reports record revenue</h1></body></html>"
        )
    return httpx.Response(404, text=f"unexpected url {url}")


def test_edgar_provider_returns_fresh_8ks_with_exhibit_body() -> None:
    provider = SecEdgarProvider(
        lookback_hours=36, throttle_s=0, transport=httpx.MockTransport(edgar_handler)
    )
    items = provider.fetch(["VCYT", "NTRA", "ZZZUNKNOWN"])

    assert len(items) == 1  # 10-Q skipped, stale 8-K skipped, NTRA quiet, unknown skipped
    item = items[0]
    assert item.id == f"edgar-{ACCESSION_FLAT}"
    assert item.source == "edgar" and item.feed == "EDGAR 8-K"
    assert item.ticker_guess == "VCYT"
    assert "results of operations" in item.title  # item-code 2.02 label, 9.01 elided
    assert "record revenue" in item.raw_text  # press exhibit preferred
    assert item.url.endswith("ex99_1.htm")
    assert item.ts <= NOW


def test_edgar_provider_survives_per_ticker_failures() -> None:
    def flaky(req: httpx.Request) -> httpx.Response:
        if "company_tickers" in str(req.url):
            return edgar_handler(req)
        return httpx.Response(500)

    provider = SecEdgarProvider(throttle_s=0, transport=httpx.MockTransport(flaky))
    assert provider.fetch(["VCYT"]) == []  # logged, not raised


# ---------------------------------------------------------------------------- Exa


def exa_handler(req: httpx.Request) -> httpx.Response:
    assert req.headers.get("x-api-key") == "test-key"
    body = json.loads(req.content)
    assert body["category"] == "news"
    assert body["numResults"] >= 1
    assert "startPublishedDate" in body
    return httpx.Response(
        200,
        json={
            "results": [
                {
                    "id": "r1",
                    "title": "Guardant Health slides on Medicare rate news",
                    "url": "https://news.example.com/guardant-rate",
                    "publishedDate": _iso(NOW - timedelta(hours=4)),
                    "text": "Guardant Health fell after CMS proposed a rate change.",
                },
                {
                    "id": "r2",
                    "title": "Undated story is dropped",
                    "url": "https://news.example.com/undated",
                    "text": "no publishedDate field",
                },
            ]
        },
    )


def test_exa_provider_maps_results_and_drops_undated() -> None:
    provider = ExaNewsProvider(
        companies=COMPANIES,
        watch=["ArteraAI"],
        api_key="test-key",
        num_results=5,
        transport=httpx.MockTransport(exa_handler),
    )
    items = provider.search(list(COMPANIES), ["diagnostics", "genomics"])

    assert len(items) == 1  # two queries, same result deduped; undated dropped
    item = items[0]
    assert item.source == "news"
    assert item.feed == "news.example.com"
    assert item.ticker_guess == "GH"
    assert item.id.startswith("exa-")


def test_exa_provider_requires_key_and_surfaces_http_errors() -> None:
    no_key = ExaNewsProvider(companies=COMPANIES, api_key=None)
    no_key.api_key = None  # defeat any ambient EXA_API_KEY in the environment
    with pytest.raises(RuntimeError, match="EXA_API_KEY"):
        no_key.search(["VCYT"], [])

    boom = ExaNewsProvider(
        companies=COMPANIES,
        api_key="test-key",
        transport=httpx.MockTransport(lambda req: httpx.Response(401, text="bad key")),
    )
    with pytest.raises(RuntimeError, match="HTTP 401"):
        boom.search(["VCYT"], [])
