"""Real-provider tests against mocked HTTP transports (httpx.MockTransport).

These pin the wire formats each integration depends on — SEC submissions API
shapes, RSS/Atom parsing, the Exa /search request/response, the Yahoo chart
payload — without touching the network, so they run in CI exactly like
everything else.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from pipeline.providers.edgar import SecEdgarProvider
from pipeline.providers.exa_news import ExaNewsProvider
from pipeline.providers.rss import HttpRSSProvider
from pipeline.providers.util import infer_ticker, strip_tags
from pipeline.providers.yahoo_quotes import DEFAULT_SIGMA, YahooQuoteProvider
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


# -------------------------------------------------------------------- Yahoo quotes

DAY = timedelta(days=1)

# 21 completed sessions; today's in-progress bar is appended by the payload
# builders and must be excluded from every trailing statistic.
DAILY_CLOSES = [100.0 + (i % 7) for i in range(21)]
DAILY_VOLUMES = [1_000_000] * 21


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def _daily_payload(symbol: str) -> dict:
    timestamps = [_epoch(NOW - (21 - i) * DAY) for i in range(21)] + [_epoch(NOW)]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"symbol": symbol, "shortName": f"{symbol} Inc."},
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                # today's partial bar: a wild close + tiny volume
                                # that would wreck sigma/avg_volume if included
                                "close": DAILY_CLOSES + [999.0],
                                "volume": DAILY_VOLUMES + [77],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


def _live_payload(symbol: str) -> dict:
    bars = [_epoch(NOW - timedelta(minutes=m)) for m in (25, 20, 15, 10, 5)]
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "shortName": f"{symbol} Inc.",
                        "chartPreviousClose": 100.0,
                        "regularMarketPrice": 104.5,
                        "regularMarketVolume": 0,  # pre-open: regular tape empty
                    },
                    "timestamp": bars,
                    "indicators": {
                        "quote": [
                            {
                                "close": [101.0, None, 103.0, None, 105.0],
                                "volume": [1000, None, 2000, None, 500],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


def yahoo_handler(req: httpx.Request) -> httpx.Response:
    assert "Mozilla" in req.headers.get("User-Agent", "")  # Yahoo rejects bot UAs
    symbol = req.url.path.rsplit("/", 1)[-1]
    params = dict(req.url.params)
    if symbol == "BOOM":
        return httpx.Response(500, text="upstream exploded")
    if symbol == "GONE":  # Yahoo's shape for unknown/delisted symbols
        return httpx.Response(
            404,
            json={
                "chart": {
                    "result": None,
                    "error": {
                        "code": "Not Found",
                        "description": "No data found, symbol may be delisted",
                    },
                }
            },
        )
    if params.get("interval") == "1d":
        assert params.get("range") == "3mo"
        return httpx.Response(200, json=_daily_payload(symbol))
    assert params.get("includePrePost") == "true"  # pre-market IS the product
    return httpx.Response(200, json=_live_payload(symbol))


def _yahoo_provider() -> YahooQuoteProvider:
    return YahooQuoteProvider(
        companies=COMPANIES, throttle_s=0, transport=httpx.MockTransport(yahoo_handler)
    )


def test_yahoo_provider_builds_premarket_quote_from_chart_api() -> None:
    (q,) = _yahoo_provider().snapshot(["VCYT"])
    assert q.ticker == "VCYT" and q.name == "Veracyte"  # universe name beats Yahoo's
    assert q.last == 105.0  # latest pre-market bar; null bars skipped
    assert q.chg_pct == 5.0  # vs chartPreviousClose 100.0
    assert q.volume == 3500  # bar sum while the regular tape is still 0
    assert q.avg_volume == 1_000_000  # today's partial 77-share bar excluded
    moves = [(b - a) / a * 100.0 for a, b in zip(DAILY_CLOSES, DAILY_CLOSES[1:])]
    assert q.sigma == pytest.approx(statistics.stdev(moves[-20:]), abs=0.01)
    assert q.flagged is False and q.rvol is None  # derived downstream, not here


def test_yahoo_provider_skips_failing_tickers_and_keeps_the_rest() -> None:
    quotes = _yahoo_provider().snapshot(["BOOM", "VCYT", "GONE"])
    assert [q.ticker for q in quotes] == ["VCYT"]


def test_yahoo_provider_raises_only_when_every_ticker_fails() -> None:
    with pytest.raises(RuntimeError, match="all 2 tickers"):
        _yahoo_provider().snapshot(["BOOM", "GONE"])
    assert _yahoo_provider().snapshot([]) == []


def test_yahoo_provider_defaults_sigma_on_short_history() -> None:
    def thin_handler(req: httpx.Request) -> httpx.Response:
        if dict(req.url.params).get("interval") == "1d":
            payload = _daily_payload("VCYT")
            result = payload["chart"]["result"][0]
            result["timestamp"] = result["timestamp"][-4:]  # 3 completed + today
            quote = result["indicators"]["quote"][0]
            quote["close"], quote["volume"] = quote["close"][-4:], quote["volume"][-4:]
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=_live_payload("VCYT"))

    provider = YahooQuoteProvider(
        companies=COMPANIES, throttle_s=0, transport=httpx.MockTransport(thin_handler)
    )
    (q,) = provider.snapshot(["VCYT"])
    assert q.sigma == DEFAULT_SIGMA  # 2 trailing moves -> not estimable
    assert q.avg_volume == 1_000_000
