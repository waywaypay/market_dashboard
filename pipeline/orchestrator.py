"""Deterministic orchestrator: source -> process -> fuse -> output.

A fixed DAG over pure, typed stages — no LLM routing anywhere. The single
LLM call lives inside the process stage's classifier provider. This module is
also the cron-style entrypoint:

    python -m pipeline.orchestrator                 # all universes, fixtures
    python -m pipeline.orchestrator --universe universes/diagnostics.yaml
    python -m pipeline.orchestrator --now 2026-06-10T06:45:00-07:00   # frozen clock (evals)

Schedule it with plain cron, e.g.  45 6 * * 1-5  make run-pipeline
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.contracts import DailyBrief, UniverseConfig
from pipeline.contracts.universe import discover_universes, load_universe
from pipeline.email_render import email_subject, render_email
from pipeline.providers.registry import ProviderSet, build_providers
from pipeline.stages.fuse import run_fuse
from pipeline.stages.output import assemble_brief, write_artifacts
from pipeline.stages.process import run_process
from pipeline.stages.source import run_source

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)


def next_market_open(now: datetime) -> datetime:
    """Next 9:30am US/Eastern at-or-after `now`, skipping weekends."""
    local = now.astimezone(MARKET_TZ)
    candidate = local.replace(
        hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0
    )
    while candidate < local or candidate.weekday() >= 5:
        candidate = (candidate + timedelta(days=1)).replace(
            hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute
        )
    return candidate


def run_universe(
    universe: UniverseConfig,
    now: datetime,
    web_public: Path,
    default: bool,
    providers: ProviderSet | None = None,
    send_email: bool = True,
) -> DailyBrief:
    providers = providers or build_providers(universe, now)

    src = run_source(universe, providers, now)
    proc = run_process(src.items, universe, providers.classifier)
    fused = run_fuse(proc.items, src.quotes, universe)
    brief = assemble_brief(
        universe=universe,
        items=fused.items,
        quotes=fused.quotes,
        priority_signals=fused.priority_signals,
        health=src.health,
        tldr=proc.tldr,
        engine=proc.engine,
        generated_at=now,
        market_open_at=next_market_open(now),
    )

    written = write_artifacts(brief, web_public, default=default)
    receipt_note = "email skipped"
    if send_email:
        try:
            receipt = providers.email.send(
                universe.delivery.recipients, email_subject(brief), render_email(brief)
            )
            receipt_note = receipt.detail
        except Exception as exc:  # transport failure must not kill the artifact
            receipt_note = f"email send failed: {type(exc).__name__}: {exc}"

    print(
        f"[{universe.id}] {brief.counts.total_items} items "
        f"({brief.counts.hot_items} hot), {sum(1 for q in brief.market if q.flagged)} flagged moves, "
        f"engine={brief.classifier_engine} -> {written[0]}; {receipt_note}"
    )
    return brief


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--universe",
        action="append",
        help="universe YAML path (repeatable); default: every universes/*.yaml",
    )
    parser.add_argument("--now", help="freeze the clock (ISO 8601) for reproducible runs")
    parser.add_argument("--out", default="web/public", help="artifact directory")
    parser.add_argument("--no-email", action="store_true", help="skip the email send step")
    args = parser.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    paths = [Path(p) for p in args.universe] if args.universe else discover_universes()
    if not paths:
        print("No universe configs found under universes/", file=sys.stderr)
        return 1

    for n, path in enumerate(paths):
        universe = load_universe(path)
        run_universe(
            universe,
            now=now,
            web_public=Path(args.out),
            default=(n == 0),  # first universe is the dashboard's default artifact
            send_email=not args.no_email,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
