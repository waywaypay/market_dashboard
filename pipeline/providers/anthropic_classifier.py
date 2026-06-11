"""Real process-stage provider: ONE batched Anthropic API call per run.

Contract (see ClassifierProvider): strict JSON only, validated against
`ClassificationBatch`; on parse/validation failure retry once, then fall back
to rule-based tagging. The run never crashes because of the LLM, and the LLM
never decides control flow — it only fills in per-item fields + the tldr.

Requires `pip install anthropic` and ANTHROPIC_API_KEY. The import is lazy so
the rest of the pipeline runs with zero keys installed.
"""

from __future__ import annotations

import os

from pipeline.contracts import RawItem, UniverseConfig
from pipeline.contracts.models import Classification, ClassificationBatch
from pipeline.providers import rules
from pipeline.providers.base import ClassifierProvider, ClassifierResult
from pipeline.providers.fixture import compose_tldr_fallback

DEFAULT_MODEL = "claude-opus-4-8"

_SYSTEM_TEMPLATE = """You are the classification engine inside a pre-market competitive-intelligence \
pipeline for IR and equity professionals covering {label}. The subject company is \
{subject_name} ({subject_ticker}); peers: {peers}.

For every input item return: the resolved ticker (or null for sector-wide \
stories), a category drawn ONLY from this taxonomy: {categories}; a \
materiality score 1-5 (5 = moves the stock / changes the thesis today, \
1 = noise); a summary; and is_subject_relevant (does this matter to \
{subject_ticker}'s competitive picture or setup today?).

Summary voice (house style):
{house_style}

Also return `tldr`: ONE sentence synthesizing the whole set — what moved and \
what the reader must know before the open.

Output STRICT JSON matching the provided schema only. No preamble, no \
markdown fences, no commentary. Echo each item's `item_id` exactly. Classify \
every item exactly once."""


def _render_items(items: list[RawItem]) -> str:
    blocks = []
    for it in items:
        blocks.append(
            f"<item id={it.id!r} source={it.feed or it.source!r} "
            f"ticker_guess={it.ticker_guess!r} ts={it.ts.isoformat()!r}>\n"
            f"{it.title}\n{it.raw_text}\n</item>"
        )
    return "\n\n".join(blocks)


class AnthropicClassifierProvider(ClassifierProvider):
    def __init__(self, model: str | None = None, max_retries: int = 1):
        self.model = model or os.environ.get("BRIEF_ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.max_retries = max_retries
        try:
            import anthropic  # lazy: optional dependency
        except ImportError as exc:  # pragma: no cover - env without the SDK
            raise RuntimeError(
                "BRIEF_CLASSIFIER=anthropic requires the SDK: pip install anthropic"
            ) from exc
        # Resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN from the environment.
        self._client = anthropic.Anthropic()

    def classify(self, items: list[RawItem], universe: UniverseConfig) -> ClassifierResult:
        if not items:
            return ClassifierResult(
                tldr=compose_tldr_fallback([], universe), classifications=[], engine="anthropic"
            )
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
                return self._reconcile(batch, items, universe)
            except Exception as exc:  # parse/validation/API failure -> retry once
                last_error = exc
        # Fall back to deterministic rules — never crash the run.
        fallback = rules.classify_batch(items, universe)
        return ClassifierResult(
            tldr=compose_tldr_fallback(fallback, universe),
            classifications=fallback,
            engine=f"rules (anthropic failed: {type(last_error).__name__})",
        )

    def _call(self, system: str, user: str) -> ClassificationBatch:
        # Structured outputs via messages.parse(): the response is constrained
        # to the ClassificationBatch JSON schema and validated by the SDK.
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=ClassificationBatch,
        )
        parsed = response.parsed_output
        if parsed is None:  # defensive: parse mode should always populate this
            text = next((b.text for b in response.content if b.type == "text"), "")
            parsed = ClassificationBatch.model_validate_json(_strip_fences(text))
        return parsed

    def _reconcile(
        self, batch: ClassificationBatch, items: list[RawItem], universe: UniverseConfig
    ) -> ClassifierResult:
        """Enforce invariants the schema can't: known ids, configured taxonomy."""
        by_id = {i.id: i for i in items}
        seen: dict[str, Classification] = {}
        for c in batch.classifications:
            if c.item_id not in by_id or c.item_id in seen:
                continue  # hallucinated or duplicate id — drop
            if c.category not in universe.categories:
                c = c.model_copy(
                    update={
                        "category": rules.classify_item(by_id[c.item_id], universe).category
                    }
                )
            seen[c.item_id] = c
        # Any item the model skipped gets rule-based tagging.
        out = [
            seen.get(i.id) or rules.classify_item(i, universe)
            for i in items
        ]
        if not batch.tldr.strip():
            raise ValueError("classifier returned an empty tldr")
        return ClassifierResult(tldr=batch.tldr.strip(), classifications=out, engine="anthropic")


def _strip_fences(text: str) -> str:
    """Defensive: tolerate ```json fences even though the prompt forbids them."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()
