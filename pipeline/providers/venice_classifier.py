"""Process-stage classifier that runs a Claude model through Venice.

Venice (https://venice.ai) resells frontier models — including Anthropic's
Claude — behind an OpenAI-compatible chat-completions endpoint (there is no
`/v1/messages`), so this provider speaks plain HTTP via the shared httpx client
and parses `choices[0].message.content`, rather than using the Anthropic SDK.

Same contract as AnthropicClassifierProvider: ONE batched call per run, strict
JSON validated against ClassificationBatch, retry once then deterministic
rule-based fallback — the run never crashes because of the LLM. The classifying
prompt, JSON parsing and reconcile step are shared verbatim with the Anthropic
path; only the transport (Venice, `VENICE_API_KEY`) and the chosen Claude model
differ.

This is what lets a deploy with no ANTHROPIC_API_KEY still get LLM-grade
relevance/materiality classification: set VENICE_API_KEY and the registry routes
classification here (BRIEF_CLASSIFIER=auto), picking a Claude model Venice serves
(claude-sonnet-4-6 by default; override with BRIEF_VENICE_CLASSIFIER_MODEL, e.g.
claude-opus-4-6).
"""

from __future__ import annotations

import os

import httpx

from pipeline.contracts import RawItem, UniverseConfig
from pipeline.contracts.models import ClassificationBatch
from pipeline.providers import rules
from pipeline.providers.anthropic_classifier import (
    _SYSTEM_TEMPLATE,
    _render_items,
    _strip_fences,
    reconcile_batch,
)
from pipeline.providers.base import ClassifierProvider, ClassifierResult
from pipeline.providers.fixture import compose_tldr_fallback
from pipeline.providers.util import make_client, match_api_key

VENICE_CHAT_URL = "https://api.venice.ai/api/v1/chat/completions"
# A Claude model Venice serves (see their model catalog). Sonnet is the
# cost-effective default for batched headline classification; switch to
# claude-opus-4-6 via BRIEF_VENICE_CLASSIFIER_MODEL for the strongest judgment.
DEFAULT_MODEL = "claude-sonnet-4-6"

# Normalized (upper-cased, separators stripped) env-var names that resolve the
# key — a near-miss spelling is the easiest way to silently get no classifier.
_KEY_NAMES = {"VENICEAPIKEY", "VENICEKEY", "VENICEAIAPIKEY", "VENICEAIKEY"}


def api_key_from_env() -> str | None:
    return match_api_key(_KEY_NAMES)


class VeniceClassifierProvider(ClassifierProvider):
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        max_retries: int = 1,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ):
        self.model = model or os.environ.get("BRIEF_VENICE_CLASSIFIER_MODEL", DEFAULT_MODEL)
        self.api_key = api_key or api_key_from_env()
        self.max_retries = max_retries
        self._client = make_client(transport=transport, timeout=timeout)

    def classify(self, items: list[RawItem], universe: UniverseConfig) -> ClassifierResult:
        if not items:
            return ClassifierResult(
                tldr=compose_tldr_fallback([], universe), classifications=[], engine="venice"
            )
        if not self.api_key:
            # Registry only routes here when a key is present; stay total anyway.
            return self._rules_fallback(items, universe, "rules (venice key missing)")

        system = _SYSTEM_TEMPLATE.format(
            label=universe.label,
            subject_name=universe.subject.name,
            subject_ticker=universe.subject.ticker,
            peers=", ".join(f"{p.name} ({p.ticker})" for p in universe.peers),
            categories=", ".join(universe.categories),
            house_style=universe.house_style.strip(),
        )
        user = (
            f"Classify these {len(items)} items:\n\n{_render_items(items)}\n\n"
            "Return JSON only."
        )

        last_error: Exception | None = None
        for _attempt in range(1 + self.max_retries):
            try:
                batch = self._call(system, user)
                return reconcile_batch(batch, items, universe, engine="venice")
            except Exception as exc:  # parse/validation/API failure -> retry once
                last_error = exc
        # Fall back to deterministic rules — never crash the run.
        return self._rules_fallback(
            items, universe, f"rules (venice failed: {type(last_error).__name__})"
        )

    def _call(self, system: str, user: str) -> ClassificationBatch:
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
                "max_tokens": 8000,
                "temperature": 0,  # classification wants determinism, not creativity
                # Venice prepends its own system prompt by default; suppress it so
                # our strict-JSON instruction fully controls the output.
                "venice_parameters": {"include_venice_system_prompt": False},
            },
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Venice chat returned HTTP {response.status_code}: {response.text[:200]}"
            )
        content = (response.json()["choices"][0]["message"]["content"] or "").strip()
        if not content:
            raise ValueError("venice returned empty content")
        return ClassificationBatch.model_validate_json(_strip_fences(content))

    def _rules_fallback(
        self, items: list[RawItem], universe: UniverseConfig, engine: str
    ) -> ClassifierResult:
        fallback = rules.classify_batch(items, universe)
        return ClassifierResult(
            tldr=compose_tldr_fallback(fallback, universe),
            classifications=fallback,
            engine=engine,
        )
