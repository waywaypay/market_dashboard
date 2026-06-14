"""Shared plumbing for real providers: HTTP client defaults, HTML stripping,
ticker inference. No provider-specific logic lives here."""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from html import unescape
from urllib.parse import quote

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


def egress_proxy() -> str | None:
    """Optional outbound proxy for providers whose vendor blocks shared cloud
    egress IPs (Yahoo 429, Stooq bot-wall). A residential proxy makes those
    requests come from a non-datacenter IP so they stop being blocked.

    Configured by env, most-specific first:
      * MASSIVE_PROXY_URL / EGRESS_PROXY_URL — a full proxy URL, used verbatim;
      * MASSIVE_KEY + MASSIVE_USERNAME — Massive (joinmassive.com) credentials,
        assembled into http://user:key@network.joinmassive.com:65534.
    Returns None when unconfigured (providers then go direct, unchanged). A key
    without a username can't authenticate, so we warn and stay direct rather
    than silently sending broken requests."""
    url = os.environ.get("MASSIVE_PROXY_URL") or os.environ.get("EGRESS_PROXY_URL")
    if url:
        return url
    key = match_api_key({"MASSIVEKEY", "MASSIVEAPIKEY"})
    if not key:
        return None
    user = (
        os.environ.get("MASSIVE_USERNAME")
        or os.environ.get("MASSIVE_PROXY_USERNAME")
        or os.environ.get("MASSIVE_USER")
    )
    if not user:
        print(
            "[egress] MASSIVE_KEY is set but no MASSIVE_USERNAME — Massive needs "
            "both (user:key); set MASSIVE_USERNAME or MASSIVE_PROXY_URL. Going direct.",
            file=sys.stderr,
        )
        return None
    host = os.environ.get("MASSIVE_PROXY_HOST", "network.joinmassive.com")
    port = os.environ.get("MASSIVE_PROXY_PORT", "65534")  # http CONNECT port
    return f"http://{quote(user, safe='')}:{quote(key, safe='')}@{host}:{port}"


def make_client(
    transport: httpx.BaseTransport | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    use_proxy: bool = False,
) -> httpx.Client:
    """Client factory; tests inject httpx.MockTransport here. Providers that get
    IP-blocked pass use_proxy=True to route through the egress proxy when one is
    configured (never when a test transport is injected)."""
    merged = {"User-Agent": user_agent()}
    if headers:
        merged.update(headers)
    proxy = egress_proxy() if (use_proxy and transport is None) else None
    return httpx.Client(
        transport=transport,
        headers=merged,
        timeout=timeout,
        follow_redirects=True,
        proxy=proxy,
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


def match_api_key(canonical_names: set[str]) -> str | None:
    """First env var whose NORMALIZED name (upper-cased, separators stripped) is
    in `canonical_names`, else None. Matching by normalized name means a key set
    under any reasonable spelling resolves the same — a near-miss like FINHUB_KEY
    vs FINNHUB_API_KEY is the easiest way to silently get an empty strip, so we
    accept them all. `canonical_names` must already be normalized."""
    for name, value in os.environ.items():
        if value and re.sub(r"[^A-Z0-9]", "", name.upper()) in canonical_names:
            return value.strip()
    return None


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
