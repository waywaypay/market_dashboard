"""THE classify/summarize handoff gate — highest-priority test.

For each shipped universe, runs the classifier(s) over the fixture RawItems
and asserts:
  1. output validates against the Classification contract (Pydantic) and every
     category is inside the universe's configured taxonomy;
  2. category / materiality-band / subject-relevance match the gold labels
     within tolerance;
  3. summaries are non-empty and terse (house-style sanity).

Tolerances differ by engine: the fixture/Claude classifier is held tight; the
rule-based fallback is held to a looser floor (it only has to be safe, not
sharp). When ANTHROPIC_API_KEY is set, the "active" classifier IS Claude, so
this gate exercises the real handoff.
"""

from __future__ import annotations

import pytest

from pipeline.evals.harness import (
    classifiers_under_test,
    fixture_raw_items,
    load_eval_universe,
    load_gold,
    score_against_gold,
)
from pipeline.providers.fixture import RulesClassifierProvider

UNIVERSES = ["diagnostics", "fintech"]

# (category, materiality-band, subject-relevance) minimum accuracy by engine class.
TIGHT = {"category": 0.90, "materiality": 0.90, "subject": 0.85}
FLOOR = {"category": 0.55, "materiality": 0.75, "subject": 0.70}


@pytest.mark.parametrize("universe_id", UNIVERSES)
def test_handoff_gate(universe_id: str) -> None:
    universe = load_eval_universe(universe_id)
    gold = load_gold(universe_id)
    items = fixture_raw_items(universe)

    # every gold item must survive the source stage (dedupe/look-ahead) and
    # reach the classifier — otherwise the gate is silently testing nothing
    present = {i.id for i in items}
    missing = sorted(set(gold) - present)
    assert not missing, f"gold ids absent from source output: {missing}"

    for classifier in classifiers_under_test(universe_id):
        result = classifier.classify(items, universe)

        # contract: pydantic already validated each Classification on construction;
        # here we assert the cross-cutting invariants the type can't encode.
        assert result.tldr.strip(), f"{result.engine}: empty tldr"
        for c in result.classifications:
            assert 1 <= c.materiality <= 5
            assert c.summary.strip(), f"{result.engine}: empty summary for {c.item_id}"
            assert len(c.summary) <= 320, f"{result.engine}: summary too long for {c.item_id}"

        score = score_against_gold(result, gold, universe)
        assert score.contract_valid, f"{score.engine}: contract violations: {score.misses}"

        bar = FLOOR if isinstance(classifier, RulesClassifierProvider) else TIGHT
        assert score.category_acc >= bar["category"], (
            f"{score.engine}: category acc {score.category_acc:.2f} < {bar['category']}; "
            f"misses={[m for m in score.misses if 'category' in m]}"
        )
        assert score.materiality_acc >= bar["materiality"], (
            f"{score.engine}: materiality acc {score.materiality_acc:.2f} < {bar['materiality']}; "
            f"misses={[m for m in score.misses if 'materiality' in m]}"
        )
        assert score.subject_acc >= bar["subject"], (
            f"{score.engine}: subject acc {score.subject_acc:.2f} < {bar['subject']}; "
            f"misses={[m for m in score.misses if 'subject' in m]}"
        )


@pytest.mark.parametrize("universe_id", UNIVERSES)
def test_materiality_floor_drops_noise(universe_id: str) -> None:
    """The seeded materiality-1 item must be classified below the floor so the
    process stage drops it — proves the floor is wired to the contract."""
    universe = load_eval_universe(universe_id)
    gold = load_gold(universe_id)
    noise = [iid for iid, lab in gold.items() if lab.materiality_max <= 1]
    assert noise, f"{universe_id}: gold set should seed a noise item"

    items = fixture_raw_items(universe)
    from pipeline.providers.registry import build_classifier

    result = build_classifier(universe_id).classify(items, universe)
    by_id = {c.item_id: c for c in result.classifications}
    for iid in noise:
        assert by_id[iid].materiality < universe.thresholds.materiality_floor
