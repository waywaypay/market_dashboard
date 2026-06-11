"""Shared plumbing for real providers: HTTP client defaults, HTML stripping,
ticker inference. No provider-specific logic lives here."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from html import unescape

import httpx

# SEC fair-access policy requires a UA with contact info; we reuse the same
# identity for RSS pulls out of politeness.
FALLBACK_USER_AGENT = "pre-market-read/0.1 (set SEC_EDGAR_USER_AGENT to your contact)"


def user_agent() -> str:
    return (
        os.environ.get("SEC_EDGAR_USER_AGENT")
        or os.environ.get("EDGAR_USER_AGENT")
        or FALLBACK_USER_AGENT
    )


def make_client(
    transport: httpx.BaseTransport | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> httpx.Client:
    """Client factory; tests inject httpx.MockTransport here."""
    merged = {"User-Agent": user_agent()}
    if headers:
        merged.update(headers)
    return httpx.Client(
        transport=transport,
        headers=merged,
        timeout=timeout,
        follow_redirects=True,
    )


_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_tags(html: str, max_chars: int = 2000) -> str:
    text = _SCRIPT_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", unescape(text)).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def infer_ticker(text: str, companies: dict[str, str]) -> str | None:
    """Best-effort ticker_guess from free text: full company name
    (case-insensitive) or exact-case ticker token. Deliberately conservative —
    the process-stage classifier resolves the final ticker; this guess only
    has to beat 'unknown'. Longest name wins ties deterministically."""
    hits: list[tuple[int, str]] = []
    for ticker, name in companies.items():
        if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            hits.append((len(name), ticker))
        elif len(ticker) >= 2 and re.search(rf"\b{re.escape(ticker)}\b", text):
            hits.append((len(ticker), ticker))
    if not hits:
        return None
    return sorted(hits, key=lambda h: (-h[0], h[1]))[0][1]


def parse_iso(value: str | None) -> datetime | None:
    """Tolerant ISO-8601 parse; returns aware UTC datetimes."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
