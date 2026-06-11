"""Eval harness helpers: load gold labels, build inputs, score classifications."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pipeline.contracts import RawItem, UniverseConfig
from pipeline.contracts.universe import load_universe
from pipeline.providers.base import ClassifierProvider, ClassifierResult
from pipeline.providers.fixture import FixtureClassifierProvider, RulesClassifierProvider
from pipeline.stages.source import run_source
from pipeline.providers.registry import build_providers

GOLD_DIR = Path(__file__).resolve().parent / "gold"
UNIVERSES_DIR = Path(__file__).resolve().parents[2] / "universes"

# Frozen clock for reproducible evals (so fixture offsets resolve identically).
EVAL_NOW = datetime(2026, 6, 10, 13, 45, tzinfo=timezone.utc)


@dataclass(frozen=True)
class GoldLabel:
    category: str
    materiality_min: int
    materiality_max: int
    is_subject_relevant: bool


def load_gold(universe_id: str) -> dict[str, GoldLabel]:
    data = json.loads((GOLD_DIR / f"{universe_id}.json").read_text(encoding="utf-8"))
    return {
        item_id: GoldLabel(**{k: v for k, v in lab.items() if not k.startswith("_")})
        for item_id, lab in data["labels"].items()
    }


def load_eval_universe(universe_id: str) -> UniverseConfig:
    return load_universe(UNIVERSES_DIR / f"{universe_id}.yaml")


def fixture_raw_items(universe: UniverseConfig) -> list[RawItem]:
    """Deduped, look-ahead-filtered items as the process stage would receive them."""
    providers = build_providers(universe.id, EVAL_NOW)
    return run_source(universe, providers, EVAL_NOW).items


@dataclass
class GateScore:
    engine: str
    total: int
    category_hits: int
    materiality_hits: int
    subject_hits: int
    contract_valid: bool
    misses: list[str]

    @property
    def category_acc(self) -> float:
        return self.category_hits / self.total if self.total else 1.0

    @property
    def materiality_acc(self) -> float:
        return self.materiality_hits / self.total if self.total else 1.0

    @property
    def subject_acc(self) -> float:
        return self.subject_hits / self.total if self.total else 1.0


def score_against_gold(
    result: ClassifierResult, gold: dict[str, GoldLabel], universe: UniverseConfig
) -> GateScore:
    by_id = {c.item_id: c for c in result.classifications}
    cat_hits = mat_hits = subj_hits = 0
    misses: list[str] = []
    contract_valid = True

    for item_id, label in gold.items():
        c = by_id.get(item_id)
        if c is None:
            misses.append(f"{item_id}: missing from classifier output")
            contract_valid = False
            continue
        # contract-level invariant: category must be in the configured taxonomy
        if c.category not in universe.categories:
            contract_valid = False
            misses.append(f"{item_id}: category {c.category!r} outside taxonomy")
        if c.category == label.category:
            cat_hits += 1
        else:
            misses.append(f"{item_id}: category {c.category!r} != gold {label.category!r}")
        if label.materiality_min <= c.materiality <= label.materiality_max:
            mat_hits += 1
        else:
            misses.append(
                f"{item_id}: materiality {c.materiality} outside "
                f"[{label.materiality_min},{label.materiality_max}]"
            )
        if c.is_subject_relevant == label.is_subject_relevant:
            subj_hits += 1
        else:
            misses.append(f"{item_id}: subject_relevant {c.is_subject_relevant} != gold")

    return GateScore(
        engine=result.engine,
        total=len(gold),
        category_hits=cat_hits,
        materiality_hits=mat_hits,
        subject_hits=subj_hits,
        contract_valid=contract_valid,
        misses=misses,
    )


def classifiers_under_test(universe_id: str) -> list[ClassifierProvider]:
    """The active classifier (fixture in CI, Claude when a key is present) plus
    the rule-based floor — both must clear the gate (at different tolerances)."""
    from pipeline.providers.registry import build_classifier

    active = build_classifier(universe_id)
    out: list[ClassifierProvider] = [active]
    if not isinstance(active, RulesClassifierProvider):
        out.append(RulesClassifierProvider())
    if not isinstance(active, FixtureClassifierProvider):
        out.append(FixtureClassifierProvider(universe_id))
    return out
