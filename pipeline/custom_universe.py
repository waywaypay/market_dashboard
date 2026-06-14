"""Build a universe spec from a user's in-app input.

A custom universe is just a universe YAML the user creates from the dashboard
(subject + peer tickers, optional sector keywords) instead of shipping one in
the repo. The taxonomy, house voice, thresholds and feeds default to sensible
generic values — the rule-based classifier resolves any taxonomy, and quotes
work keyless, so a custom universe always produces a brief without curation.

Pure functions (no I/O) so the server endpoint and tests share the same logic.
"""

from __future__ import annotations

import re

# Generic business taxonomy the rule-based classifier maps cleanly onto
# (product / partnership / regulatory / financial buckets).
DEFAULT_CATEGORIES = ["Product", "Partnership", "Regulatory", "Financial"]
DEFAULT_HOUSE_STYLE = (
    "Terse IR 'First Read' voice. 1-2 sentences per item, factual, no hedging, "
    "lead with the company and the event."
)

MAX_TICKERS = 40  # bound the pipeline cost of a single user-created universe
MAX_KEYWORDS = 12
# Tickers, including class shares and some ADRs: BRK.B, RDS-A, etc.
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


class UniverseSpecError(ValueError):
    """User input that can't be turned into a valid universe."""


def clean_ticker(raw: object) -> str | None:
    t = str(raw or "").strip().upper()
    return t if _TICKER_RE.match(t) else None


def slugify(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return s or "universe"


def unique_id(slug: str, existing_ids: set[str]) -> str:
    """`user-<slug>` (the `user-` prefix keeps custom universes sorting after the
    built-ins, so one never becomes the dashboard's default), deduped."""
    base = f"user-{slug}"
    uid = base
    n = 2
    while uid in existing_ids:
        uid = f"{base}-{n}"
        n += 1
    return uid


def build_spec(payload: dict, existing_ids: set[str]) -> dict:
    """User payload -> a universe dict ready for UniverseConfig + YAML.

    payload: {label, subject_ticker, subject_name?, peer_tickers?, sector_keywords?}
    Raises UniverseSpecError on bad input.
    """
    label = str(payload.get("label") or "").strip()
    if not 1 <= len(label) <= 60:
        raise UniverseSpecError("Universe name must be 1–60 characters.")

    subject = clean_ticker(payload.get("subject_ticker"))
    if subject is None:
        raise UniverseSpecError("A valid subject ticker is required (e.g. AAPL).")
    subject_name = str(payload.get("subject_name") or "").strip() or subject

    seen = {subject}
    peers: list[dict] = []
    for raw in payload.get("peer_tickers") or []:
        t = clean_ticker(raw)
        if t is not None and t not in seen:
            seen.add(t)
            peers.append({"ticker": t, "name": t})  # names default to the ticker
    if len(seen) > MAX_TICKERS:
        raise UniverseSpecError(f"At most {MAX_TICKERS} tickers per universe.")

    keywords = [
        str(k).strip()
        for k in (payload.get("sector_keywords") or [])
        if str(k).strip()
    ][:MAX_KEYWORDS]

    return {
        "id": unique_id(slugify(label), existing_ids),
        "label": label,
        "custom": True,
        "subject": {"ticker": subject, "name": subject_name},
        "peers": peers,
        "private_watch": [],
        "sector_keywords": keywords,
        "rss_feeds": [],
        "categories": DEFAULT_CATEGORIES,
        "house_style": DEFAULT_HOUSE_STYLE,
    }
