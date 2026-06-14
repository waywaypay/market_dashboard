"""Real First Read generator: ONE VeniceAI chat completion per run.

Venice (https://venice.ai) exposes an OpenAI-compatible chat-completions
endpoint, so this provider speaks plain HTTP via the shared httpx client — no
extra SDK to install. Contract (see FirstReadProvider): write a short narrative
morning note from the already-assembled brief; on any API/parse failure retry
once, then fall back to the deterministic fixture composer. The run never
crashes because of the LLM, and the LLM never decides control flow — the brief
is already assembled before this runs; Venice only writes the prose over it.

Requires VENICE_API_KEY (matched flexibly by normalized name, like the keyed
quote tiers, so VENICE_KEY / VENICE_AI_API_KEY / … all resolve).
"""

from __future__ import annotations

import os

import httpx

from pipeline.contracts import DailyBrief, UniverseConfig
from pipeline.providers.base import FirstReadProvider, FirstReadResult
from pipeline.providers.fixture import compose_first_read
from pipeline.providers.util import make_client, match_api_key

VENICE_CHAT_URL = "https://api.venice.ai/api/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b"

# Normalized (upper-cased, separators stripped) env-var names that resolve the
# key — a near-miss spelling is the easiest way to silently get no note.
_KEY_NAMES = {"VENICEAPIKEY", "VENICEKEY", "VENICEAIAPIKEY", "VENICEAIKEY"}


def api_key_from_env() -> str | None:
    return match_api_key(_KEY_NAMES)


_SYSTEM_TEMPLATE = """You are the markets editor for a pre-market \
competitive-intelligence brief covering {label}. The subject company is \
{subject_name} ({subject_ticker}).

Write "today's First Read": a tight, scannable morning note — one short \
paragraph, 2 to 4 sentences — that a busy IR or equity professional reads in \
~15 seconds before the open. Lead with what actually moved and why it matters; \
name tickers and the size of the moves; then the one or two things to watch. \
Synthesize across the inputs rather than listing them, and never invent facts \
beyond what you are given.

House voice:
{house_style}

Output the paragraph as plain prose only — no markdown, no headings, no bullet \
points, no preamble, no sign-off."""


def _render_digest(brief: DailyBrief) -> str:
    """The structured context Venice writes from — everything it needs and
    nothing it must invent. All of it is already in the assembled brief."""
    lines = [f"Working one-line synthesis (from the classifier): {brief.tldr}", ""]

    movers = [q for q in brief.market if q.flagged]
    if movers:
        lines.append("Unusual pre-market moves (flagged):")
        for q in movers:
            rvol = f", {q.rvol:.1f}x volume" if q.rvol is not None else ""
            lines.append(
                f"- {q.name} ({q.ticker}): {q.chg_pct:+.1f}%{rvol} "
                f"[{q.flag_reason or 'unusual'}]"
            )
    else:
        lines.append("No unusual pre-market moves flagged this run.")
    lines.append("")

    if brief.priority_signals:
        lines.append("Top priority signals (material + price-linked):")
        for s in brief.priority_signals[:6]:
            who = s.company or s.ticker or "Sector"
            lines.append(f"- [{s.category}, materiality {s.materiality}] {who}: {s.summary}")
        lines.append("")

    lines.append(
        f"Scope: {brief.counts.total_items} items cleared the floor, "
        f"{brief.counts.hot_items} hot."
    )
    return "\n".join(lines)


class VeniceFirstReadProvider(FirstReadProvider):
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        max_retries: int = 1,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ):
        self.model = model or os.environ.get("BRIEF_VENICE_MODEL", DEFAULT_MODEL)
        self.api_key = api_key or api_key_from_env()
        self.max_retries = max_retries
        self._client = make_client(transport=transport, timeout=timeout)

    def generate(self, brief: DailyBrief, universe: UniverseConfig) -> FirstReadResult:
        if not self.api_key:
            # Registry only routes here when a key is present; stay total anyway.
            return FirstReadResult(
                text=compose_first_read(brief), engine="fixture (venice key missing)"
            )

        system = _SYSTEM_TEMPLATE.format(
            label=universe.label,
            subject_name=universe.subject.name,
            subject_ticker=universe.subject.ticker,
            house_style=universe.house_style.strip(),
        )
        user = _render_digest(brief)

        last_error: Exception | None = None
        for _attempt in range(1 + self.max_retries):
            try:
                text = self._call(system, user)
                if text:
                    return FirstReadResult(text=text, engine="venice")
                raise ValueError("venice returned empty content")
            except Exception as exc:  # API/parse failure -> retry once
                last_error = exc
        # Fall back to the deterministic composer — never block the brief.
        return FirstReadResult(
            text=compose_first_read(brief),
            engine=f"fixture (venice failed: {type(last_error).__name__})",
        )

    def _call(self, system: str, user: str) -> str:
        response = self._client.post(
            VENICE_CHAT_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 320,
                "temperature": 0.4,
                # Venice prepends its own system prompt by default; suppress it
                # so our house-style instruction fully controls the voice.
                "venice_parameters": {"include_venice_system_prompt": False},
            },
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Venice chat returned HTTP {response.status_code}: {response.text[:200]}"
            )
        data = response.json()
        return (data["choices"][0]["message"]["content"] or "").strip()
