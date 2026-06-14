"""Shared plumbing for real providers: HTTP client defaults, HTML stripping,
ticker inference. No provider-specific logic lives here."""

from __future__ import annotations

import os
import re
import unicodedata
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


# Separators a publisher commonly tacks onto a headline ("… - Finextra").
_TITLE_SEPARATORS = (" - ", " — ", " – ", " | ", " · ")


def clean_title(title: str, feed: str | None = None) -> str:
    """Normalize a raw feed/search headline so it renders clean.

    Feed titles arrive with HTML entities (``&amp;``, ``&#8217;``), stray
    markup, doubled whitespace, and — frequently — a trailing publisher
    attribution. We unescape + strip + collapse (via ``strip_tags``), then trim
    a trailing ``- <publisher>`` only when it echoes the known feed label, which
    keeps the strip safe against titles that legitimately contain a dash."""
    text = strip_tags(title, max_chars=300)
    if feed:
        feed_l = feed.strip().lower()
        for sep in _TITLE_SEPARATORS:
            if feed_l and text.lower().endswith(f"{sep}{feed_l}"):
                text = text[: -(len(sep) + len(feed_l))].rstrip()
                break
    return text


# Function words distinctive enough to a given language to be a reliable signal.
# English markers are deliberately exclusive of words shared with Romance/German
# headlines (no "a"/"an"/"on"/"in"/"no"…); foreign markers exclude any token that
# also reads as ordinary English ("die"/"per"/"plus"/"est"), so a match in either
# set means what it says.
_EN_STOPWORDS = frozenset(
    """the and of to is are was were has have had will would with for from says said
    after over about this that these those into amid its their than what why how when
    where new report reports""".split()
)
_FOREIGN_STOPWORDS = frozenset(
    """de la el los las del una uma para con por como más mais não são dos das pela pelo
    le les des du une pour avec sur dans aux qui que ce ces leur cette
    der das und für mit von dem den ein eine auf ist sich nicht wird bei zum zur
    il lo gli della dei che del di sono anche""".split()
)
_WORD_RE = re.compile(r"[a-zà-öø-ÿ]+")


def is_probably_english(*texts: str) -> bool:
    """Conservative, dependency-free language gate for headlines.

    Returns False only when the text is clearly *not* English: dominated by a
    non-Latin script (CJK / Cyrillic / Arabic / Greek / …), or — for Latin-script
    text — carrying foreign function words and no English ones. Anything
    ambiguous or too short to judge is kept (True), so genuine English headlines
    are never dropped; the cost is letting the occasional borderline item pass.
    """
    text = " ".join(t for t in texts if t).strip()
    if not text:
        return True

    latin = non_latin = 0
    for ch in text:
        if not ch.isalpha():
            continue
        try:
            is_latin = "LATIN" in unicodedata.name(ch)
        except ValueError:  # unnamed character — treat as non-Latin
            is_latin = False
        if is_latin:
            latin += 1
        else:
            non_latin += 1
    letters = latin + non_latin
    if letters and non_latin / letters > 0.30:
        return False  # a non-Latin script dominates the headline

    words = _WORD_RE.findall(text.lower())
    if len(words) < 4:
        return True  # too short to judge by function words — keep
    wordset = set(words)
    if wordset & _EN_STOPWORDS:
        return True
    if wordset & _FOREIGN_STOPWORDS:
        return False
    return True  # all content words (e.g. proper nouns) — keep


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
