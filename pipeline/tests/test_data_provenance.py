"""Data provenance: a brief must always say whether its data is real.

The committed-fixture-artifact incident: a deploy whose refresh fails (or
that never runs one) must never present synthetic data as live market data.
These tests pin the two halves of the fix — provider modes recorded into
the artifact, and the server answering honestly when no artifact exists.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from pipeline.evals.harness import EVAL_NOW, UNIVERSES_DIR
from pipeline.contracts.universe import load_universe
from pipeline.orchestrator import run_universe
from pipeline.providers.registry import build_providers
from pipeline.stages.output import derive_data_mode


def test_derive_data_mode() -> None:
    real = {s: "real" for s in ("rss", "edgar", "news", "quotes")}
    assert derive_data_mode(real) == "real"
    assert derive_data_mode({s: "fixture" for s in real}) == "fixture"
    assert derive_data_mode({**real, "quotes": "fixture"}) == "mixed"
    # missing sources never count as real — provenance must not overclaim
    assert derive_data_mode({}) == "fixture"
    assert derive_data_mode({"rss": "real"}) == "mixed"
    # email is transport, not data: it never taints the data mode
    assert derive_data_mode({**real, "email": "fixture"}) == "real"


def test_registry_records_modes(monkeypatch) -> None:
    universe = load_universe(UNIVERSES_DIR / "diagnostics.yaml")
    monkeypatch.setenv("BRIEF_PROVIDERS", "real")
    monkeypatch.setenv("BRIEF_QUOTES", "fixture")
    monkeypatch.setenv("BRIEF_EMAIL", "fixture")
    modes = build_providers(universe, EVAL_NOW).modes
    assert modes["rss"] == modes["edgar"] == modes["news"] == "real"
    assert modes["quotes"] == "fixture"
    assert modes["email"] == "fixture"


def test_fixture_run_stamps_fixture_mode(monkeypatch, tmp_path) -> None:
    for var in ("BRIEF_PROVIDERS", "BRIEF_RSS", "BRIEF_EDGAR", "BRIEF_NEWS", "BRIEF_QUOTES"):
        monkeypatch.delenv(var, raising=False)  # library default: fixture
    universe = load_universe(UNIVERSES_DIR / "diagnostics.yaml")
    brief = run_universe(
        universe,
        now=EVAL_NOW,
        web_public=tmp_path,
        default=True,
        providers=build_providers(universe, EVAL_NOW),
        send_email=False,
    )
    assert brief.data_mode == "fixture"
    assert brief.provider_modes["quotes"] == "fixture"
    written = json.loads((tmp_path / "brief.json").read_text())
    assert written["data_mode"] == "fixture"


def test_artifact_predating_provenance_reads_as_fixture() -> None:
    """Old artifacts (no data_mode field) must not pass for real data."""
    from pipeline.contracts import DailyBrief

    minimal = {
        "universe_id": "u",
        "generated_at": "2026-06-11T12:00:00Z",
        "market_open_at": "2026-06-11T13:30:00Z",
        "tldr": "t",
        "counts": {"total_items": 0, "hot_items": 0},
        "market": [],
        "priority_signals": [],
        "by_company": {},
        "sector_headlines": [],
        "source_status": [],
        "universe_label": "U",
        "subject_ticker": "X",
        "subject_name": "X Corp",
        "categories": [],
        "display_tz": "UTC",
        "classifier_engine": "fixture",
    }
    assert DailyBrief.model_validate(minimal).data_mode == "fixture"


# -- server honesty when no artifact exists ----------------------------------


@pytest.fixture()
def empty_server(monkeypatch, tmp_path):
    import pipeline.serve as serve

    monkeypatch.setattr(serve, "PUBLIC", tmp_path / "public")
    monkeypatch.setattr(serve, "DIST", tmp_path / "dist")
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


def test_missing_artifact_answers_refresh_status_not_404(empty_server: int) -> None:
    for path in ("/brief.json", "/briefs/diagnostics.json", "/universes.json"):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{empty_server}{path}")
            raise AssertionError("expected HTTP 503")
        except urllib.error.HTTPError as err:
            assert err.code == 503
            body = json.loads(err.read())
            assert body["ok"] is False
            assert "refresh" in body  # status + detail the UI can show verbatim
            assert body["refresh"]["status"] in ("pending", "running", "ok", "failed")


def test_status_endpoint_reports_refresh_state(empty_server: int) -> None:
    with urllib.request.urlopen(f"http://127.0.0.1:{empty_server}/api/status") as resp:
        body = json.loads(resp.read())
    assert body["ok"] is True
    assert body["refresh"]["status"] in ("pending", "running", "ok", "failed")
    assert body["refresh"]["detail"]


def test_refresh_failure_is_recorded(monkeypatch) -> None:
    import pipeline.serve as serve

    def boom(*args, **kwargs):
        raise RuntimeError("vendor melted")

    monkeypatch.setattr("pipeline.orchestrator.run_universe", boom)
    result = serve.refresh_artifacts()
    assert result["ok"] is False
    assert "vendor melted" in result["detail"]
    state = serve.refresh_status()
    assert state["status"] == "failed"
    assert "vendor melted" in state["detail"]
    assert state["at"]  # stamped, so the UI can say how stale the failure is
