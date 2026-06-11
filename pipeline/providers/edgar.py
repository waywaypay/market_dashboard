"""Real SEC EDGAR provider: 8-Ks + press exhibits via the submissions API.

Flow per run:
  1. resolve ticker -> CIK once via https://www.sec.gov/files/company_tickers.json
  2. GET https://data.sec.gov/submissions/CIK##########.json per ticker
  3. keep 8-K / 8-K/A filings accepted inside the lookback window
  4. for each, fetch the filing index and prefer the EX-99.* press exhibit
     body (falling back to the primary document) for raw_text

SEC fair-access policy: a User-Agent with contact info is mandatory (set
SEC_EDGAR_USER_AGENT, e.g. "yourapp/1.0 you@example.com") and requests are
throttled well under the 10 req/s ceiling. No key required — EDGAR is free.
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

from pipeline.contracts import RawItem
from pipeline.providers.base import EdgarProvider
from pipeline.providers.util import make_client, parse_iso, strip_tags

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}"

# 8-K item codes -> human-readable event labels (the title the classifier sees)
ITEM_LABELS = {
    "1.01": "material definitive agreement",
    "1.02": "termination of material agreement",
    "1.03": "bankruptcy or receivership",
    "2.01": "completion of acquisition or disposition",
    "2.02": "results of operations",
    "2.03": "creation of direct financial obligation",
    "2.05": "costs associated with exit activities",
    "2.06": "material impairments",
    "3.01": "delisting / listing-standards notice",
    "4.01": "change in auditor",
    "4.02": "non-reliance on prior financials",
    "5.01": "change in control",
    "5.02": "officer or director changes",
    "5.03": "amendments to charter or bylaws",
    "5.07": "shareholder vote results",
    "7.01": "Regulation FD disclosure",
    "8.01": "other events",
    "9.01": "financial statements and exhibits",
}

_EXHIBIT_NAME_RE = re.compile(r"ex[-_.]?99", re.IGNORECASE)


class SecEdgarProvider(EdgarProvider):
    def __init__(
        self,
        lookback_hours: float | None = None,
        max_filings: int = 25,
        throttle_s: float = 0.13,
        transport: httpx.BaseTransport | None = None,
    ):
        self.lookback = timedelta(
            hours=lookback_hours
            if lookback_hours is not None
            else float(os.environ.get("EDGAR_LOOKBACK_HOURS", "36"))
        )
        self.max_filings = max_filings
        self.throttle_s = throttle_s
        self._client = make_client(transport=transport)
        self._ticker_map: dict[str, int] | None = None

    # -- public interface ---------------------------------------------------

    def fetch(self, tickers: list[str]) -> list[RawItem]:
        cutoff = datetime.now(timezone.utc) - self.lookback
        ticker_to_cik = self._load_ticker_map()

        items: list[RawItem] = []
        for ticker in tickers:
            cik = ticker_to_cik.get(ticker.upper())
            if cik is None:
                continue  # not an SEC registrant we can resolve — skip honestly
            try:
                items.extend(self._filings_for(ticker, cik, cutoff))
            except Exception as exc:
                print(f"[edgar] {ticker}: {type(exc).__name__}: {exc}", file=sys.stderr)
            if len(items) >= self.max_filings:
                break
        return items[: self.max_filings]

    # -- internals ------------------------------------------------------------

    def _get(self, url: str) -> httpx.Response:
        time.sleep(self.throttle_s)  # stay far under SEC's 10 req/s ceiling
        response = self._client.get(url)
        response.raise_for_status()
        return response

    def _load_ticker_map(self) -> dict[str, int]:
        if self._ticker_map is None:
            data = self._get(TICKER_MAP_URL).json()
            self._ticker_map = {
                row["ticker"].upper(): int(row["cik_str"]) for row in data.values()
            }
        return self._ticker_map

    def _filings_for(self, ticker: str, cik: int, cutoff: datetime) -> list[RawItem]:
        sub = self._get(SUBMISSIONS_URL.format(cik=cik)).json()
        company = sub.get("name") or ticker
        recent = sub.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])

        out: list[RawItem] = []
        for i, form in enumerate(forms[:60]):  # recent[] is newest-first
            if form not in ("8-K", "8-K/A"):
                continue
            accepted = parse_iso(recent["acceptanceDateTime"][i]) or parse_iso(
                f"{recent['filingDate'][i]}T12:00:00+00:00"
            )
            if accepted is None or accepted < cutoff:
                continue
            accession = recent["accessionNumber"][i].replace("-", "")
            primary = recent["primaryDocument"][i]
            item_codes = [c.strip() for c in (recent.get("items", [""] * len(forms))[i] or "").split(",") if c.strip()]
            url, body = self._fetch_body(cik, accession, primary)
            title = _title(company, form, item_codes)
            out.append(
                RawItem(
                    id=f"edgar-{accession}",
                    source="edgar",
                    feed="EDGAR 8-K",
                    url=url,
                    title=title,
                    raw_text=body or title,
                    ts=accepted,
                    ticker_guess=ticker,
                )
            )
        return out

    def _fetch_body(self, cik: int, accession: str, primary: str) -> tuple[str, str]:
        """(url, text) — prefer the EX-99.* press exhibit over the 8-K shell."""
        base = ARCHIVES_BASE.format(cik=cik, accession=accession)
        doc_url = f"{base}/{primary}"
        try:
            index = self._get(f"{base}/index.json").json()
            names = [
                entry.get("name", "")
                for entry in index.get("directory", {}).get("item", [])
            ]
            exhibit = next(
                (
                    n
                    for n in names
                    if _EXHIBIT_NAME_RE.search(n) and n.lower().endswith((".htm", ".html"))
                ),
                None,
            )
            target = f"{base}/{exhibit}" if exhibit else doc_url
            text = strip_tags(self._get(target).text, max_chars=2000)
            return target, text
        except Exception:
            # body fetch is best-effort; the titled filing is still signal
            return doc_url, ""


def _title(company: str, form: str, item_codes: list[str]) -> str:
    if item_codes:
        labels = [ITEM_LABELS.get(c, f"item {c}") for c in item_codes if c != "9.01"]
        if labels:
            return f"{company} files {form}: {', '.join(labels)}"
    return f"{company} files {form}"
