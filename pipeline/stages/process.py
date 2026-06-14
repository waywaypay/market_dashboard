"""Process stage: the classification LLM touchpoint in the pipeline.

(items, universe, classifier) -> ProcessResult. The classifier provider owns
its own failure handling (retry once -> rule-based fallback), so this stage
is total: it always returns typed Items. Items below the configured
materiality floor are dropped here.

(The other LLM touchpoint is the First Read narration, which runs after the
brief is assembled — see the orchestrator and providers/venice_first_read.py.)
"""

from __future__ import annotations

from pydantic import BaseModel

from pipeline.contracts import Item, RawItem, UniverseConfig
from pipeline.providers.base import ClassifierProvider


class ProcessResult(BaseModel):
    items: list[Item]
    tldr: str
    engine: str


def _company_for(ticker: str | None, item: RawItem, universe: UniverseConfig) -> str | None:
    if ticker is not None:
        return universe.companies.get(ticker, ticker)
    # private-watch companies have no ticker; recover the display name from text
    text = f"{item.title} {item.raw_text}".lower()
    for name in universe.private_watch:
        if name.lower() in text:
            return name
    return None


def run_process(
    items: list[RawItem], universe: UniverseConfig, classifier: ClassifierProvider
) -> ProcessResult:
    result = classifier.classify(items, universe)
    by_id = {c.item_id: c for c in result.classifications}
    floor = universe.thresholds.materiality_floor

    out: list[Item] = []
    for raw in items:
        c = by_id.get(raw.id)
        if c is None or c.materiality < floor:
            continue  # unclassified (shouldn't happen) or below the floor
        ticker = c.ticker if c.ticker in universe.companies else None
        out.append(
            Item(
                id=raw.id,
                ticker=ticker,
                company=_company_for(ticker, raw, universe),
                category=c.category,
                materiality=c.materiality,
                summary=c.summary,
                title=raw.title,
                url=raw.url,
                source=raw.feed or raw.source,
                ts=raw.ts,
                is_subject_relevant=c.is_subject_relevant,
            )
        )
    return ProcessResult(items=out, tldr=result.tldr, engine=result.engine)
