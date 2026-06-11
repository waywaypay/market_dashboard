"""Deterministic rule-based classifier.

Two jobs:
  1. Fallback when the LLM call fails twice (the run must never crash).
  2. A floor implementation the eval gate holds to looser tolerances.

Categories are config-driven, so the rules map keywords to *canonical
buckets* and then resolve each bucket to the closest category name in the
universe's taxonomy.
"""

from __future__ import annotations

import re

from pipeline.contracts import RawItem, UniverseConfig
from pipeline.contracts.models import Classification

# canonical bucket -> trigger keywords (lowercase, matched on title+text)
_BUCKET_KEYWORDS: dict[str, list[str]] = {
    "regulatory": [
        "fda", "cms", "medicare", "medicaid", "coverage", "reimbursement", "ldt",
        "clearance", "approval", "510(k)", "breakthrough device", "sec ", "cfpb",
        "doj", "investigation", "inquiry", "subpoena", "consent order", "license",
        "regulator", "rule", "compliance", "occ", "charter", "prior auth",
    ],
    "clinical": [
        "study", "trial", "data", "publication", "published", "nejm", "jama",
        "lancet", "validation", "enrollment", "readout", "cohort", "sensitivity",
        "specificity", "abstract", "asco", "guideline", "nccn", "clinical",
    ],
    "financial": [
        "earnings", "guidance", "revenue", "preliminary", "preannounce", "q1",
        "q2", "q3", "q4", "offering", "raises", "series", "ipo", "buyback",
        "acquisition", "acquire", "merger", "restatement", "convertible", "debt",
        "delinquen", "charge-off", "originations", "funding round", "valuation",
        "repurchase", "refinanc", "interchange",
    ],
    "commercial": [
        "launch", "partnership", "partner", "deal", "contract", "agreement",
        "expansion", "expands", "volume", "customers", "payer", "rollout",
        "milestone", "integration", "distribution", "wins", "collaboration",
    ],
    "product": [
        "launch", "launches", "feature", "app", "card", "api", "assay", "test",
        "platform", "product", "checkout", "wallet", "integration", "rollout",
    ],
    "partnership": [
        "partnership", "partner", "collaboration", "alliance", "agreement",
        "teams up", "deal with", "joint",
    ],
}

# bucket aliases so config taxonomies resolve even when names differ
_CATEGORY_ALIASES: dict[str, list[str]] = {
    "clinical": ["clinical"],
    "commercial": ["commercial"],
    "regulatory": ["regulatory", "policy"],
    "financial": ["financial", "finance"],
    "product": ["product"],
    "partnership": ["partnership", "partnerships"],
}

_HIGH_IMPACT = [
    "medicare coverage", "expands coverage", "coverage expansion", "fda approval",
    "fda clearance", "acquisition", "merger", "acquire", "guidance rais",
    "guidance cut", "restatement", "halt", "above guidance", "below guidance",
    "ceo", "bank partnership", "funding partnership", "rate cut", "price cut",
    "withdraw", "recall",
]
_MEDIUM_IMPACT = [
    "earnings", "preliminary", "partnership", "launch", "publication", "study",
    "trial", "data", "contract", "license", "inquiry", "investigation",
    "series", "funding", "expansion", "milestone", "guideline", "nccn", "rule",
]
_LOW_SIGNAL = [
    "to present", "conference", "webinar", "fireside", "award", "appoints vp",
    "named to", "will attend", "poster", "investor day",
]


def _resolve_category(bucket: str, universe: UniverseConfig) -> str:
    """Map a canonical bucket onto the universe's configured taxonomy."""
    lowered = {c.lower(): c for c in universe.categories}
    for alias in _CATEGORY_ALIASES.get(bucket, [bucket]):
        if alias in lowered:
            return lowered[alias]
    # No direct name match — fall back to the last configured category
    # (taxonomies conventionally end with the catch-all, e.g. Financial).
    return universe.categories[-1]


def _score_bucket(text: str, universe: UniverseConfig) -> str:
    available = {c.lower() for c in universe.categories}
    scores: dict[str, int] = {}
    for bucket, words in _BUCKET_KEYWORDS.items():
        # only consider buckets that can resolve to a real category by name
        aliases = _CATEGORY_ALIASES.get(bucket, [bucket])
        if not any(a in available for a in aliases):
            continue
        scores[bucket] = sum(1 for w in words if w in text)
    if not scores or max(scores.values()) == 0:
        return "commercial" if "commercial" in available else next(iter(available))
    # deterministic tie-break: highest score, then bucket name
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _materiality(text: str) -> int:
    if any(w in text for w in _LOW_SIGNAL) and not any(w in text for w in _HIGH_IMPACT):
        return 1
    score = 2
    if any(w in text for w in _MEDIUM_IMPACT):
        score += 1
    if any(w in text for w in _HIGH_IMPACT):
        score += 2
    return min(score, 5)


def _subject_relevant(item: RawItem, materiality: int, universe: UniverseConfig) -> bool:
    text = f"{item.title} {item.raw_text}".lower()
    if item.ticker_guess == universe.subject.ticker:
        return True
    if universe.subject.name.lower() in text or universe.subject.ticker.lower() in text:
        return True
    if item.ticker_guess is None:  # sector-wide stories shape the subject's setup
        return True
    return materiality >= 4  # big peer events always matter to the subject


def _summary(item: RawItem) -> str:
    """First sentence of the raw text, trimmed — terse, factual, no hedging."""
    first = re.split(r"(?<=[.!?])\s+", item.raw_text.strip())[0].strip()
    if len(first) > 220:
        first = first[:217].rstrip() + "..."
    return first


def classify_item(item: RawItem, universe: UniverseConfig) -> Classification:
    text = f"{item.title} {item.raw_text}".lower()
    materiality = _materiality(text)
    return Classification(
        item_id=item.id,
        ticker=item.ticker_guess,
        category=_resolve_category(_score_bucket(text, universe), universe),
        materiality=materiality,
        summary=_summary(item),
        is_subject_relevant=_subject_relevant(item, materiality, universe),
    )


def classify_batch(items: list[RawItem], universe: UniverseConfig) -> list[Classification]:
    return [classify_item(i, universe) for i in items]
