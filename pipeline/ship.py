"""Ship the First Read email for an already-generated artifact.

Used by the dashboard's "Generate today's First Read" action (the Vite dev
server shells out to this), and usable directly:

    python -m pipeline.ship --universe diagnostics

Loads web/public/briefs/<id>.json, renders the email from that same artifact,
and sends it via the configured EmailProvider (fixture: writes .html to
out/emails/). Prints a single JSON result line for machine consumption.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.contracts import DailyBrief
from pipeline.contracts.universe import load_universe
from pipeline.email_render import email_subject, render_email
from pipeline.providers.registry import build_providers


def ship(universe_id: str, web_public: Path = Path("web/public")) -> dict:
    artifact = web_public / "briefs" / f"{universe_id}.json"
    if not artifact.exists():
        return {
            "ok": False,
            "detail": f"No artifact for '{universe_id}' — run `make run-pipeline` first.",
        }
    brief = DailyBrief.model_validate_json(artifact.read_text(encoding="utf-8"))
    config_path = Path("universes") / f"{universe_id}.yaml"
    if not config_path.exists():
        return {"ok": False, "detail": f"No universe config at {config_path}."}
    universe = load_universe(config_path)
    providers = build_providers(universe, brief.generated_at)
    try:
        receipt = providers.email.send(
            universe.delivery.recipients, email_subject(brief), render_email(brief)
        )
    except Exception as exc:
        return {"ok": False, "detail": f"email send failed: {type(exc).__name__}: {exc}"}
    return {"ok": receipt.accepted, "detail": receipt.detail}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", required=True, help="universe id, e.g. diagnostics")
    parser.add_argument("--out", default="web/public", help="artifact directory")
    args = parser.parse_args(argv)
    result = ship(args.universe, Path(args.out))
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
