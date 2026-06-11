"""Production server smoke: routing, artifact freshness, ship endpoint."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from pipeline.serve import (
    ARTIFACT_PREFIXES,
    DIST,
    PUBLIC,
    Handler,
    _default_to_real_providers,
    resolve_static,
)


def test_server_defaults_to_real_data_pulls(monkeypatch) -> None:
    """A deploy with no BRIEF_* env (manually-created Render services never
    see render.yaml) must pull real data, not silently serve fixtures."""
    # setenv first so monkeypatch snapshots + restores the original state
    monkeypatch.setenv("BRIEF_PROVIDERS", "placeholder")
    monkeypatch.setenv("BRIEF_EMAIL", "placeholder")
    monkeypatch.delenv("BRIEF_PROVIDERS")
    monkeypatch.delenv("BRIEF_EMAIL")

    _default_to_real_providers()
    assert os.environ["BRIEF_PROVIDERS"] == "real"
    assert os.environ["BRIEF_EMAIL"] == "fixture"  # no real transport yet

    monkeypatch.setenv("BRIEF_PROVIDERS", "fixture")  # explicit demo mode wins
    _default_to_real_providers()
    assert os.environ["BRIEF_PROVIDERS"] == "fixture"


def test_resolve_static_routes_artifacts_to_public() -> None:
    target = resolve_static("/brief.json")
    if target is not None:  # artifacts exist after any pipeline run
        assert PUBLIC in target.parents
    target = resolve_static("/briefs/diagnostics.json")
    if target is not None:
        assert PUBLIC in target.parents


def test_resolve_static_rejects_traversal_and_unknown() -> None:
    assert resolve_static("/../pyproject.toml") is None
    assert resolve_static("/briefs/../../etc/passwd") is None


def test_resolve_static_spa_fallback_only_when_built() -> None:
    target = resolve_static("/some/spa/route")
    if (DIST / "index.html").is_file():
        assert target == DIST / "index.html"
    else:
        assert target is None


@pytest.fixture()
def server_port():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


def _get(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as resp:
        return resp.status, dict(resp.headers), resp.read()


def test_healthz(server_port: int) -> None:
    status, _, body = _get(server_port, "/healthz")
    assert status == 200 and body == b"ok"


def test_artifacts_served_with_no_store(server_port: int) -> None:
    if not (PUBLIC / "brief.json").is_file():
        pytest.skip("no artifact generated yet")
    status, headers, body = _get(server_port, "/brief.json")
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body)["universe_id"]


def test_ship_rejects_bad_universe_id(server_port: int) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{server_port}/api/ship",
        data=json.dumps({"universe": "../evil"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req)
        raise AssertionError("expected HTTP 400")
    except urllib.error.HTTPError as err:
        assert err.code == 400
        assert json.loads(err.read())["ok"] is False


def test_missing_dist_serves_self_heal_page(monkeypatch, tmp_path, server_port: int) -> None:
    """An under-configured deploy (no web/dist) must answer with the status
    page, not a bare error — and never spawn npm when auto-build is off."""
    import pipeline.serve as serve

    monkeypatch.setattr(serve, "DIST", tmp_path / "nodist")
    monkeypatch.setenv("BRIEF_AUTO_BUILD", "0")
    try:
        _get(server_port, "/")
        raise AssertionError("expected HTTP 503")
    except urllib.error.HTTPError as err:
        assert err.code == 503
        body = err.read().decode()
        assert "Build Command" in body  # tells the operator the durable fix
        assert "npm ci" in body


def test_artifact_prefixes_cover_everything_the_pipeline_writes() -> None:
    # write_artifacts produces exactly these three shapes; if that changes,
    # this test forces the server's freshness overlay to follow
    assert "/brief.json" in ARTIFACT_PREFIXES
    assert "/briefs/" in ARTIFACT_PREFIXES
    assert "/universes.json" in ARTIFACT_PREFIXES
