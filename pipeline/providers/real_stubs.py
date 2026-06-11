"""Stubs for real source/email providers.

Each implements the vendor-neutral interface and documents exactly what a
real implementation needs. They raise NotImplementedError with actionable
messages; the source stage catches provider failures and reports them as
SourceHealth(status="failed"), so running with BRIEF_PROVIDERS=real does not
crash — it produces an honest, empty brief. No keys are required to run the
project; these exist so real integrations slot in behind the same contracts.

Interface conformance is tested in pipeline/tests/test_provider_conformance.py.
"""

from __future__ import annotations

from pipeline.contracts import EmailReceipt, Quote, RawItem
from pipeline.providers.base import (
    EdgarProvider,
    EmailProvider,
    NewsProvider,
    QuoteProvider,
    RSSProvider,
)


class HttpRSSProvider(RSSProvider):
    """TODO(real-provider): fetch + parse the configured feeds.

    Implementation sketch:
      * map feed labels -> feed URLs (extend the universe YAML with urls)
      * GET each feed (httpx), parse with feedparser
      * RawItem(id=sha1(url), source="rss", feed=label, ts=entry.published)
    """

    def fetch(self, feeds: list[str]) -> list[RawItem]:
        raise NotImplementedError(
            "HttpRSSProvider: implement feed fetching (httpx + feedparser). "
            "Run with BRIEF_PROVIDERS=fixture in the meantime."
        )


class SecEdgarProvider(EdgarProvider):
    """TODO(real-provider): 8-Ks + press exhibits via the SEC submissions API.

    Implementation sketch:
      * resolve ticker -> CIK via https://www.sec.gov/files/company_tickers.json
      * GET https://data.sec.gov/submissions/CIK{cik:0>10}.json (set a real
        User-Agent per SEC fair-access policy; throttle to <10 req/s)
      * keep form=8-K since the last run; pull EX-99.* press exhibits
      * RawItem(source="edgar", feed="EDGAR 8-K", ticker_guess=ticker)
    """

    def fetch(self, tickers: list[str]) -> list[RawItem]:
        raise NotImplementedError(
            "SecEdgarProvider: implement the SEC submissions API integration."
        )


class SearchNewsProvider(NewsProvider):
    """TODO(real-provider): ticker/keyword news search behind any vendor
    (Bing News, NewsAPI, Marketaux...). Map results into RawItem; the dedupe
    in the source stage handles overlap with RSS/EDGAR.
    """

    def search(self, tickers: list[str], sector_keywords: list[str]) -> list[RawItem]:
        raise NotImplementedError(
            "SearchNewsProvider: implement a news-search vendor integration."
        )


class MarketDataQuoteProvider(QuoteProvider):
    """TODO(real-provider): pre-market snapshot from any market-data vendor
    (Polygon, Finnhub, IEX...). Must populate last, chg_pct, volume,
    avg_volume and sigma (trailing ~20d stdev of daily % moves) — rvol and
    unusual-move flags are derived downstream, in the deterministic fuse stage.
    """

    def snapshot(self, tickers: list[str]) -> list[Quote]:
        raise NotImplementedError(
            "MarketDataQuoteProvider: implement a market-data vendor integration."
        )


class SmtpEmailProvider(EmailProvider):
    """TODO(real-provider): send via SMTP or an API vendor (SES, Postmark...).
    The HTML arrives fully rendered and inline-styled; this class only
    transports it.
    """

    def send(self, recipients: list[str], subject: str, html: str) -> EmailReceipt:
        raise NotImplementedError(
            "SmtpEmailProvider: implement transport (smtplib / SES / Postmark)."
        )
