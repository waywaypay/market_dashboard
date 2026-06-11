"""Remaining real-provider stub (email transport).

Every data pull has a real implementation now: RSS (rss.py), SEC EDGAR
(edgar.py), news search (exa_news.py) and quotes (yahoo_quotes.py). Email
transport — the one outbound side effect — still needs a vendor decision;
this stub implements the vendor-neutral interface and raises
NotImplementedError with an actionable message. The orchestrator catches
email-send failures, so selecting it does not crash a run — it degrades
honestly.

Interface conformance is tested in pipeline/tests/test_provider_conformance.py.
"""

from __future__ import annotations

from pipeline.contracts import EmailReceipt
from pipeline.providers.base import EmailProvider


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
