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
from pipeline.providers.yahoo_quotes import (
    _HISTORY_CACHE,
    _SESSION,
    DEFAULT_SIGMA,
    YahooQuoteProvider,
)
from pipeline.contracts.universe import RSSFeed


@pytest.fixture(autouse=True)
def _fresh_yahoo_state():
    """Both module-level caches must not leak between tests."""
    _HISTORY_CACHE.clear()
    _SESSION.update(crumb=None, cookies=None)
    yield
    _HISTORY_CACHE.clear()
    _SESSION.update(crumb=None, cookies=None)

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
    """Chart API only — handshake/v7 requests 404, forcing the chart fallback."""
    if "/v8/finance/chart/" not in req.url.path:
        return httpx.Response(404, text="no v7 here")
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
        companies=COMPANIES,
        throttle_s=0,
        max_attempts=1,  # failure tests should not sit in backoff sleeps
        backoff_s=0,
        transport=httpx.MockTransport(yahoo_handler),
    )


def test_yahoo_provider_builds_premarket_quote_via_chart_fallback() -> None:
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


def test_yahoo_provider_fails_over_to_mirror_host() -> None:
    def flaky_edge(req: httpx.Request) -> httpx.Response:
        if "/v8/finance/chart/" not in req.url.path:
            return httpx.Response(404, text="no v7 here")
        if req.url.host == "query1.finance.yahoo.com":
            return httpx.Response(502, text="bad edge")
        assert req.url.host == "query2.finance.yahoo.com"
        return yahoo_handler(req)

    provider = YahooQuoteProvider(
        companies=COMPANIES, throttle_s=0, transport=httpx.MockTransport(flaky_edge)
    )
    (q,) = provider.snapshot(["VCYT"])
    assert q.last == 105.0  # query2 served what query1 couldn't


def test_yahoo_provider_reports_flat_when_nothing_traded_today() -> None:
    def quiet_open(req: httpx.Request) -> httpx.Response:
        if "/v8/finance/chart/" not in req.url.path:
            return httpx.Response(404, text="no v7 here")
        if dict(req.url.params).get("interval") == "1d":
            return httpx.Response(200, json=_daily_payload("VCYT"))
        payload = _live_payload("VCYT")
        result = payload["chart"]["result"][0]
        result["timestamp"] = []
        result["indicators"]["quote"][0].update(close=[], volume=[])
        return httpx.Response(200, json=payload)

    provider = YahooQuoteProvider(
        companies=COMPANIES, throttle_s=0, transport=httpx.MockTransport(quiet_open)
    )
    (q,) = provider.snapshot(["VCYT"])
    assert q.last == 104.5  # meta regularMarketPrice — the prior close
    assert q.chg_pct == 0.0  # yesterday's move must not masquerade as today's
    assert q.volume == 0


def test_yahoo_provider_backs_off_through_a_429_and_spares_the_mirror() -> None:
    seen: list[str] = []  # hosts of chart requests only

    def limited(req: httpx.Request) -> httpx.Response:
        if "/v8/finance/chart/" not in req.url.path:
            return httpx.Response(404, text="no v7 here")
        seen.append(req.url.host)
        if len(seen) == 1:
            return httpx.Response(429, text="slow down", headers={"Retry-After": "0"})
        return yahoo_handler(req)

    provider = YahooQuoteProvider(
        companies=COMPANIES,
        throttle_s=0,
        backoff_s=0,
        transport=httpx.MockTransport(limited),
    )
    (q,) = provider.snapshot(["VCYT"])
    assert q.last == 105.0  # retry after backing off succeeded
    # rate-limited responses must not trigger an immediate mirror hit —
    # the budget is per source IP, so that only digs the hole deeper
    assert seen[:2] == ["query1.finance.yahoo.com", "query1.finance.yahoo.com"]


def test_yahoo_provider_caches_daily_history_across_runs() -> None:
    daily_hits = {"n": 0}

    def counting(req: httpx.Request) -> httpx.Response:
        if "/v8/finance/chart/" in req.url.path and (
            dict(req.url.params).get("interval") == "1d"
        ):
            daily_hits["n"] += 1
        return yahoo_handler(req)

    transport = httpx.MockTransport(counting)
    for _ in range(2):  # two refreshes = two provider instances, same day
        provider = YahooQuoteProvider(
            companies=COMPANIES, throttle_s=0, transport=transport
        )
        (q,) = provider.snapshot(["VCYT"])
        assert q.avg_volume == 1_000_000
    assert daily_hits["n"] == 1  # sigma/avg_volume reused the cached history


def test_yahoo_provider_defaults_sigma_on_short_history() -> None:
    def thin_handler(req: httpx.Request) -> httpx.Response:
        if "/v8/finance/chart/" not in req.url.path:
            return httpx.Response(404, text="no v7 here")
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


# ------------------------------------------------- Yahoo batched v7 (primary path)

CRUMB = "test-crumb"


def _v7_row(symbol: str, **overrides) -> dict:
    row = {
        "symbol": symbol,
        "shortName": f"{symbol} Inc.",
        "marketState": "PRE",
        "preMarketPrice": 105.0,
        "regularMarketPrice": 100.0,
        "regularMarketPreviousClose": 100.0,
        "regularMarketVolume": 3500,
        "averageDailyVolume3Month": 1_000_000,
    }
    row.update(overrides)
    return row


def _yahoo_routes(v7_rows: dict[str, dict], chart=yahoo_handler):
    """Full happy-path transport: cookie -> crumb -> batched v7 (+ charts)."""

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.url.host == "fc.yahoo.com":
            # like the real thing: scoped to .yahoo.com so query1/query2 get it
            return httpx.Response(
                404, headers={"set-cookie": "A3=abc; Domain=.yahoo.com; Path=/"}
            )
        if path.endswith("/v1/test/getcrumb"):
            return httpx.Response(200, text=CRUMB)
        if path.endswith("/v7/finance/quote"):
            params = dict(req.url.params)
            assert params.get("crumb") == CRUMB  # the crumb must ride along
            assert "A3=abc" in req.headers.get("cookie", "")  # with its cookie
            symbols = params["symbols"].split(",")
            rows = [v7_rows[s] for s in symbols if s in v7_rows]
            return httpx.Response(200, json={"quoteResponse": {"result": rows}})
        return chart(req)

    return handler


def test_yahoo_provider_prices_the_whole_universe_in_one_quote_call() -> None:
    v7_calls = {"n": 0}
    rows = {
        "VCYT": _v7_row("VCYT"),
        "NTRA": _v7_row("NTRA", marketState="REGULAR", regularMarketPrice=98.0),
    }
    base = _yahoo_routes(rows)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/v7/finance/quote"):
            v7_calls["n"] += 1
        return base(req)

    provider = YahooQuoteProvider(
        companies=COMPANIES, throttle_s=0, transport=httpx.MockTransport(handler)
    )
    by = {q.ticker: q for q in provider.snapshot(["VCYT", "NTRA"])}

    assert v7_calls["n"] == 1  # one request priced every ticker
    assert by["VCYT"].name == "Veracyte" and by["VCYT"].last == 105.0
    assert by["VCYT"].chg_pct == 5.0  # preMarketPrice vs previous close
    assert by["VCYT"].volume == 3500 and by["VCYT"].avg_volume == 1_000_000
    moves = [(b - a) / a * 100.0 for a, b in zip(DAILY_CLOSES, DAILY_CLOSES[1:])]
    assert by["VCYT"].sigma == pytest.approx(statistics.stdev(moves[-20:]), abs=0.01)
    assert by["NTRA"].last == 98.0  # regular session price outside pre-market
    assert by["NTRA"].chg_pct == -2.0


def test_yahoo_provider_uses_post_market_price_after_the_close() -> None:
    rows = {"VCYT": _v7_row("VCYT", marketState="POSTPOST", postMarketPrice=99.0)}
    provider = YahooQuoteProvider(
        companies=COMPANIES, throttle_s=0, transport=httpx.MockTransport(_yahoo_routes(rows))
    )
    (q,) = provider.snapshot(["VCYT"])
    assert q.last == 99.0 and q.chg_pct == -1.0  # vs previous close


def test_yahoo_provider_ships_prices_even_when_history_is_throttled() -> None:
    def throttled_history(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down", headers={"Retry-After": "0"})

    provider = YahooQuoteProvider(
        companies=COMPANIES,
        throttle_s=0,
        max_attempts=1,
        backoff_s=0,
        transport=httpx.MockTransport(
            _yahoo_routes({"VCYT": _v7_row("VCYT")}, chart=throttled_history)
        ),
    )
    (q,) = provider.snapshot(["VCYT"])
    assert q.last == 105.0  # the price survives
    assert q.sigma == DEFAULT_SIGMA  # history degraded; it must not kill the quote
    assert q.avg_volume == 1_000_000  # v7's 3-month average fills in


def test_yahoo_provider_respects_its_time_budget() -> None:
    chart_hits = {"n": 0}

    def black_hole(req: httpx.Request) -> httpx.Response:
        if "/v8/finance/chart/" not in req.url.path:
            return httpx.Response(404, text="no v7 here")
        chart_hits["n"] += 1
        return httpx.Response(429, text="slow down", headers={"Retry-After": "60"})

    provider = YahooQuoteProvider(
        companies=COMPANIES,
        throttle_s=0,
        backoff_s=999,  # would sleep ~forever if the budget didn't gate retries
        deadline_s=0,
        transport=httpx.MockTransport(black_hole),
    )
    with pytest.raises(RuntimeError, match="all 2 tickers"):
        provider.snapshot(["VCYT", "NTRA"])
    # over budget: history skipped, one live attempt for the first ticker,
    # remaining tickers dropped — a wedged vendor can't wedge the refresh
    assert chart_hits["n"] == 1


def test_yahoo_provider_does_the_crumb_handshake_once_per_process() -> None:
    crumb_calls = {"n": 0}
    base = _yahoo_routes({"VCYT": _v7_row("VCYT")})

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/v1/test/getcrumb"):
            crumb_calls["n"] += 1
        return base(req)

    transport = httpx.MockTransport(handler)
    for _ in range(2):  # two refreshes = two provider instances, same process
        provider = YahooQuoteProvider(companies=COMPANIES, throttle_s=0, transport=transport)
        (q,) = provider.snapshot(["VCYT"])
        assert q.last == 105.0
    assert crumb_calls["n"] == 1  # cookie+crumb reused across instances
