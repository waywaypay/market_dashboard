"""Chain quote providers: first one to return data wins.

Vendors fail in uncorrelated ways (Yahoo rate-limits shared cloud IPs;
others lag or lack pre-market tape), so the real quote source is a chain —
the strip stays REAL on the best vendor currently answering, and upgrades
itself back to the primary automatically on the next refresh.
"""

from __future__ import annotations

import sys

from pipeline.contracts import Quote
from pipeline.providers.base import QuoteProvider


class FallbackQuoteProvider(QuoteProvider):
    def __init__(self, *providers: QuoteProvider):
        if not providers:
            raise ValueError("FallbackQuoteProvider needs at least one provider")
        self.providers = providers

    def snapshot(self, tickers: list[str]) -> list[Quote]:
        errors: list[str] = []
        for provider in self.providers:
            name = type(provider).__name__
            try:
                quotes = provider.snapshot(tickers)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                print(f"[quotes] {name} failed — trying next vendor", file=sys.stderr)
                continue
            if quotes:
                if errors:
                    print(f"[quotes] served by fallback {name}", file=sys.stderr)
                return quotes
            errors.append(f"{name}: returned no quotes")
        raise RuntimeError("all quote vendors failed — " + "; ".join(errors))
