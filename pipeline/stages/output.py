"""Output stage: assemble the DailyBrief artifact + write it for the web app.

The artifact is the single source of truth — the dashboard renders it
verbatim and the email renders from the same object, so they never disagree.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pipeline.contracts import Counts, DailyBrief, Item, Quote, SourceHealth, UniverseConfig

# Providers whose mode determines whether the DATA in the brief is real.
# Email transport is delivery, not data, so it never taints data_mode.
DATA_SOURCES = ("rss", "edgar", "news", "quotes")


def derive_data_mode(provider_modes: dict[str, str]) -> str:
    """real | fixture | mixed, from the modes the registry actually selected.
    Unknown/missing sources count as fixture — provenance must never overclaim."""
    seen = {provider_modes.get(source, "fixture") for source in DATA_SOURCES}
    if seen == {"real"}:
        return "real"
    if seen == {"fixture"}:
        return "fixture"
    return "mixed"


def _ordered_companies(universe: UniverseConfig, items: list[Item]) -> list[str]:
    """Display order: subject first, then peers (config order), then private
    watch / anything else that showed up, alphabetically."""
    configured = [universe.subject.name] + [p.name for p in universe.peers]
    present = {i.company for i in items if i.company}
    extras = sorted(present - set(configured))
    return [c for c in configured if c in present] + extras


def assemble_brief(
    universe: UniverseConfig,
    items: list[Item],
    quotes: list[Quote],
    priority_signals: list[Item],
    health: list[SourceHealth],
    tldr: str,
    engine: str,
    generated_at: datetime,
    market_open_at: datetime,
    provider_modes: dict[str, str] | None = None,
) -> DailyBrief:
    hot = universe.thresholds.hot_materiality

    by_company: dict[str, list[Item]] = {}
    for company in _ordered_companies(universe, items):
        rows = [i for i in items if i.company == company]
        rows.sort(key=lambda i: (-i.materiality, i.ts, i.id))
        by_company[company] = rows

    sector = [i for i in items if i.company is None and i.ticker is None]
    sector.sort(key=lambda i: (-i.materiality, i.ts, i.id))

    # subject pinned first; peers follow config order
    order = {t: n for n, t in enumerate(universe.tickers)}
    market = sorted(quotes, key=lambda q: order.get(q.ticker, 999))

    return DailyBrief(
        universe_id=universe.id,
        generated_at=generated_at,
        market_open_at=market_open_at,
        tldr=tldr,
        counts=Counts(
            total_items=len(items),
            hot_items=sum(1 for i in items if i.materiality >= hot),
        ),
        market=market,
        priority_signals=priority_signals,
        by_company=by_company,
        sector_headlines=sector,
        source_status=health,
        universe_label=universe.label,
        subject_ticker=universe.subject.ticker,
        subject_name=universe.subject.name,
        categories=universe.categories,
        display_tz=universe.delivery.tz,
        classifier_engine=engine,
        data_mode=derive_data_mode(provider_modes or {}),  # type: ignore[arg-type]
        provider_modes=provider_modes or {},
    )


def write_artifacts(
    brief: DailyBrief, web_public: str | Path, default: bool, custom: bool = False
) -> list[Path]:
    """Write briefs/<id>.json (+ brief.json for the default universe) and
    refresh the universes.json manifest the dashboard's selector reads."""
    public = Path(web_public)
    briefs_dir = public / "briefs"
    briefs_dir.mkdir(parents=True, exist_ok=True)

    payload = brief.model_dump_json(indent=2)
    written = [briefs_dir / f"{brief.universe_id}.json"]
    written[0].write_text(payload, encoding="utf-8")
    if default:
        path = public / "brief.json"
        path.write_text(payload, encoding="utf-8")
        written.append(path)

    manifest_path = public / "universes.json"
    manifest: list[dict] = []
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = []
    entry = {
        "id": brief.universe_id,
        "label": brief.universe_label,
        "subject_ticker": brief.subject_ticker,
        "subject_name": brief.subject_name,
        "custom": custom,  # user-created universes are deletable from the selector
    }
    manifest = [m for m in manifest if m.get("id") != brief.universe_id] + [entry]
    manifest.sort(key=lambda m: m["id"])
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    written.append(manifest_path)
    return written
