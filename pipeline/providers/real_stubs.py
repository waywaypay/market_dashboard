"""Remaining real-provider stubs (quotes + email transport).

RSS, EDGAR and news search have real implementations now (rss.py, edgar.py,
exa_news.py). These two still need a vendor decision; they implement the
vendor-neutral interface and raise NotImplementedError with actionable
messages. The source stage reports a failed quotes pull as
SourceHealth(status="failed") and the orchestrator catches email-send
failures, so running with these selected does not crash — it degrades
honestly.

Interface conformance is tested in pipeline/tests/test_provider_conformance.py.
"""

from __future__ import annotations

from pipeline.contracts import EmailReceipt, Quote
from pipeline.providers.base import EmailProvider, QuoteProvider


class MarketDataQuoteProvider(QuoteProvider):
    """TODO(real-provider): pre-market snapshot from any market-data vendor
    (Polygon, Finnhub, IEX...). Must populate last, chg_pct, volume,
    avg_volume and sigma (trailing ~20d stdev of daily % moves) — rvol and
    unusual-move flags are derived downstream, in the deterministic fuse stage.
    """

    def snapshot(self, tickers: list[str]) -> list[Quote]:
        raise NotImplementedError(
            "MarketDataQuoteProvider: implement a market-data vendor integration "
            "(or run quotes on fixtures: BRIEF_QUOTES=fixture)."
        )


class SmtpEmailProvider(EmailProvider):
    """TODO(real-provider): send via SMTP or an API vendor (SES, Postmark...).
    The HTML arrives fully rendered and inline-styled; this class only
    transports it.
    """

    def send(self, recipients: list[str], subject: str, html: str) -> EmailReceipt:
        raise NotImplementedError(
            "SmtpEmailProvider: implement transport (smtplib / SES / Postmark), "
            "or keep BRIEF_EMAIL=fixture to write .html to out/emails/."
        )
