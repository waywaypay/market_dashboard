"""Production server — the deploy entrypoint (Render/Fly/any host with a PORT).

    python -m pipeline.serve

One process does everything the local `make dev` split does:
  * serves the built dashboard from web/dist (immutable-cached assets)
  * serves artifacts (brief.json, briefs/*, universes.json) from web/public
    with no-store headers, so a pipeline re-run updates the cockpit without
    a redeploy
  * POST /api/ship     -> re-render + send the First Read for a universe
  * POST /api/refresh  -> re-run the pipeline now (the dashboard's ↻ button)
  * GET  /api/status   -> last refresh outcome (running/ok/failed + why)
  * GET  /healthz      -> 200 for platform health checks
  * a missing artifact answers 503 with the refresh status instead of 404 —
    the dashboard shows "first refresh running/failed: <why>", never a blank
    page and never stale demo data (fixture artifacts are not committed)
  * refreshes the artifact at boot and every BRIEF_REFRESH_MINUTES (default
    30; 0 disables) — emails are never sent by scheduled refreshes, only by
    the explicit ship action

Stdlib only (ThreadingHTTPServer): this is a low-traffic research cockpit,
not a CDN. Provider selection comes from the BRIEF_* env vars, but unlike
the library default (fixtures), the deployed server defaults to REAL data
pulls — manually-created Render services never see render.yaml's env vars,
and a production deploy that silently serves synthetic data is wrong.
Setting BRIEF_PROVIDERS=fixture explicitly still gives a demo deploy.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "web" / "dist"
PUBLIC = REPO / "web" / "public"
UNIVERSES = REPO / "universes"
ARTIFACT_PREFIXES = ("/brief.json", "/briefs/", "/universes.json")

_refresh_lock = threading.Lock()
_create_lock = threading.Lock()  # serialize on-demand universe builds

# Last refresh outcome, served by /api/status and attached to artifact-miss
# responses so the UI can say WHY there is no data yet instead of rendering
# nothing (or worse, something synthetic).
_refresh_state_lock = threading.Lock()
_refresh_state = {
    "status": "pending",  # pending|running|ok|failed
    "detail": "no refresh has run yet",
    "at": None,  # ISO timestamp of the last completed refresh
}


def _set_refresh_state(status: str, detail: str, stamp: bool = False) -> None:
    with _refresh_state_lock:
        _refresh_state["status"] = status
        _refresh_state["detail"] = detail
        if stamp:
            _refresh_state["at"] = datetime.now(timezone.utc).isoformat()


def refresh_status() -> dict:
    with _refresh_state_lock:
        return dict(_refresh_state)

# Self-healing web build: when a deploy's build command didn't compile the
# dashboard (e.g. a manually-created Render service left on `poetry install`),
# the server builds it at boot instead of 503ing forever. Costs ~a minute on
# the first boot of such a deploy; never triggers when dist/ already exists.
# Disable with BRIEF_AUTO_BUILD=0.
_web_build = {"status": "idle", "detail": ""}  # idle|building|failed|done
_web_build_lock = threading.Lock()

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".map": "application/json",
}


def refresh_artifacts() -> dict:
    """Re-run the deterministic pipeline for every universe. Never raises —
    the server must stay up even when a scheduled run fails. One universe
    crashing must not block the rest, so each gets its own try/except."""
    from pipeline.contracts.universe import discover_universes, load_universe
    from pipeline.orchestrator import run_universe

    if not _refresh_lock.acquire(blocking=False):
        return {"ok": False, "detail": "a refresh is already running"}
    try:
        _set_refresh_state("running", "refresh in progress")
        now = datetime.now(timezone.utc)
        done: list[str] = []
        failed: list[str] = []
        for n, path in enumerate(discover_universes(REPO / "universes")):
            universe = load_universe(path)
            try:
                run_universe(
                    universe,
                    now=now,
                    web_public=PUBLIC,
                    default=(n == 0),
                    send_email=False,  # email leaves only via the explicit ship action
                )
                done.append(universe.id)
            except Exception as exc:
                traceback.print_exc()
                failed.append(f"{universe.id}: {type(exc).__name__}: {exc}")
        if failed:
            detail = f"refresh failed for {'; '.join(failed)}"
            if done:
                detail += f" (refreshed {', '.join(done)})"
            _set_refresh_state("failed", detail, stamp=True)
            return {"ok": False, "detail": detail}
        detail = f"refreshed {', '.join(done)} at {now.isoformat()}"
        _set_refresh_state("ok", detail, stamp=True)
        return {"ok": True, "detail": detail}
    except Exception as exc:
        traceback.print_exc()
        detail = f"refresh failed: {type(exc).__name__}: {exc}"
        _set_refresh_state("failed", detail, stamp=True)
        return {"ok": False, "detail": detail}
    finally:
        _refresh_lock.release()


def _valid_id(uid: str) -> bool:
    return bool(uid) and ".." not in uid and "/" not in uid and (
        uid.replace("-", "").replace("_", "").isalnum()
    )


def create_universe(payload: dict) -> dict:
    """Build a user-defined universe from {label, subject_ticker, peer_tickers?,
    sector_keywords?}: write its YAML to universes/ and run the pipeline once so
    its brief exists immediately. Custom universes always pull real data (no
    fixtures exist for arbitrary tickers); email is never sent."""
    from pipeline.contracts.universe import discover_universes, load_universe
    from pipeline.custom_universe import UniverseSpecError, build_spec
    from pipeline.orchestrator import run_universe

    with _create_lock:
        try:
            existing = {p.stem for p in discover_universes(UNIVERSES)}
            spec = build_spec(payload, existing)
        except UniverseSpecError as exc:
            return {"ok": False, "detail": str(exc), "status": 400}
        except Exception as exc:  # malformed payload shape
            return {"ok": False, "detail": f"invalid request: {exc}", "status": 400}

        UNIVERSES.mkdir(parents=True, exist_ok=True)
        path = UNIVERSES / f"{spec['id']}.yaml"
        path.write_text(
            yaml.safe_dump(spec, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        try:
            run_universe(
                load_universe(path),
                now=datetime.now(timezone.utc),
                web_public=PUBLIC,
                default=False,  # a custom universe never takes over brief.json
                send_email=False,
            )
        except Exception as exc:
            traceback.print_exc()
            path.unlink(missing_ok=True)  # don't leave a half-built universe behind
            return {
                "ok": False,
                "detail": f"failed to build universe: {type(exc).__name__}: {exc}",
                "status": 500,
            }
        return {
            "ok": True,
            "id": spec["id"],
            "label": spec["label"],
            "detail": f"created {spec['label']}",
        }


def delete_universe(uid: str) -> dict:
    """Remove a user-created universe — its YAML, brief, and manifest entry.
    Built-in universes (no `user-` prefix) are never deletable."""
    if not _valid_id(uid) or not uid.startswith("user-"):
        return {"ok": False, "detail": "only custom universes can be deleted", "status": 400}
    with _create_lock:
        (UNIVERSES / f"{uid}.yaml").unlink(missing_ok=True)
        (PUBLIC / "briefs" / f"{uid}.json").unlink(missing_ok=True)
        manifest_path = PUBLIC / "universes.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = [m for m in manifest if m.get("id") != uid]
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            except json.JSONDecodeError:
                pass
    return {"ok": True, "detail": f"deleted {uid}"}


def ensure_web_built() -> None:
    """Kick off a background `npm ci && npm run build` when dist/ is absent."""
    if (DIST / "index.html").is_file() or os.environ.get("BRIEF_AUTO_BUILD", "1") == "0":
        return
    with _web_build_lock:
        if _web_build["status"] in ("building", "done"):
            return
        npm = shutil.which("npm")
        if npm is None:
            _web_build.update(
                status="failed",
                detail="npm is not available in this runtime — set the deploy "
                "Build Command instead (see below).",
            )
            return
        _web_build.update(status="building", detail="npm ci && npm run build")

    def run() -> None:
        try:
            for args, timeout in (
                ([npm, "ci", "--no-audit", "--no-fund"], 600),
                ([npm, "run", "build"], 600),
            ):
                step = subprocess.run(
                    args,
                    cwd=REPO / "web",
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                if step.returncode != 0:
                    tail = (step.stderr or step.stdout or "").strip()[-400:]
                    _web_build.update(
                        status="failed", detail=f"`{' '.join(args[1:])}` failed: {tail}"
                    )
                    print(f"[serve] web build failed: {tail}", file=sys.stderr)
                    return
            _web_build.update(status="done", detail="")
            print("[serve] web build complete — dashboard is live", file=sys.stderr)
        except Exception as exc:
            _web_build.update(status="failed", detail=f"{type(exc).__name__}: {exc}")
            print(f"[serve] web build crashed: {exc}", file=sys.stderr)

    threading.Thread(target=run, name="web-build", daemon=True).start()


_BUILD_CMD = "pip install -r requirements.txt && cd web && npm ci && npm run build"


def _not_built_page() -> tuple[int, bytes]:
    """Status page while the dashboard compiles (or instructions if it can't)."""
    status = _web_build["status"]
    refresh = '<meta http-equiv="refresh" content="6">' if status == "building" else ""
    if status == "building":
        headline = "Compiling the dashboard…"
        body = (
            "This deploy's build command didn't produce <code>web/dist</code>, so the "
            "server is building it now (<code>npm ci &amp;&amp; npm run build</code>). "
            "This page refreshes itself — the cockpit appears in about a minute."
        )
    else:
        headline = "Dashboard not built."
        body = escape(_web_build["detail"] or "The web bundle is missing from this deploy.")
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">{refresh}
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Pre-Market Read</title></head>
<body style="margin:0;background:#F7F8FA;color:#12161C;font-family:Inter,system-ui,sans-serif">
<div style="background:#12161C;color:#fff;padding:16px 24px;font-weight:600">Pre-Market Read</div>
<div style="max-width:600px;margin:14vh auto 0;padding:0 24px">
<h1 style="font-size:20px">{headline}</h1>
<p style="line-height:1.6;color:#475467">{body}</p>
<p style="line-height:1.6;color:#475467">To skip this on every cold start, set the deploy's
Build Command to:</p>
<pre style="background:#fff;border:1px solid #E4E7EC;padding:12px;overflow-x:auto;font-size:12px">{_BUILD_CMD}</pre>
</div></body></html>"""
    return (503, html.encode())


def resolve_static(path: str) -> Path | None:
    """Map a URL path to a file: artifacts from web/public (fresh), everything
    else from web/dist, with an index.html fallback for extensionless paths."""
    clean = path.split("?", 1)[0]
    if ".." in clean:
        return None
    if clean == "/":
        clean = "/index.html"
    if clean.startswith(ARTIFACT_PREFIXES):
        candidate = PUBLIC / clean.lstrip("/")
        if candidate.is_file():
            return candidate
    candidate = DIST / clean.lstrip("/")
    if candidate.is_file():
        return candidate
    if "." not in clean.rsplit("/", 1)[-1]:  # SPA route -> shell
        index = DIST / "index.html"
        return index if index.is_file() else None
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "pre-market-read/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[serve] {self.address_string()} {fmt % args}", file=sys.stderr)

    # -- GET ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path == "/healthz":
            self._send(200, "text/plain; charset=utf-8", b"ok")
            return
        if self.path.split("?", 1)[0] == "/api/status":
            self._send_json(200, {"ok": True, "refresh": refresh_status()})
            return
        target = resolve_static(self.path)
        if target is None:
            if self.path.split("?", 1)[0].startswith(ARTIFACT_PREFIXES):
                # No artifact on disk (fixture artifacts are not committed, so
                # a fresh deploy has none until the first real refresh lands).
                # Tell the dashboard what's happening instead of 404ing.
                state = refresh_status()
                self._send_json(
                    503,
                    {
                        "ok": False,
                        "detail": (
                            "no artifact generated yet — "
                            f"refresh {state['status']}: {state['detail']}"
                        ),
                        "refresh": state,
                    },
                )
                return
            if not (DIST / "index.html").is_file():
                ensure_web_built()  # self-heal under-configured deploys
                status, body = _not_built_page()
                self._send(status, "text/html; charset=utf-8", body)
                return
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return

        body = target.read_bytes()
        ctype = MIME.get(target.suffix.lower(), "application/octet-stream")
        if self.path.split("?", 1)[0].startswith(ARTIFACT_PREFIXES):
            cache = "no-store"  # artifacts must always be the latest run
        elif "/assets/" in self.path:
            cache = "public, max-age=31536000, immutable"  # content-hashed
        else:
            cache = "no-cache"
        self._send(200, ctype, body, cache)

    # -- POST -----------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/refresh":
            result = refresh_artifacts()
            self._send_json(200 if result["ok"] else 500, result)
            return
        if self.path == "/api/universe":  # create a custom universe + build it
            result = create_universe(self._read_json_body())
            status = result.pop("status", 200 if result["ok"] else 500)
            self._send_json(status, result)
            return
        if self.path == "/api/universe/delete":
            result = delete_universe(str(self._read_json_body().get("id", "")))
            status = result.pop("status", 200 if result["ok"] else 500)
            self._send_json(status, result)
            return
        if self.path == "/api/ship":
            universe = str(self._read_json_body().get("universe", ""))
            if not universe.replace("-", "").replace("_", "").isalnum():
                self._send_json(400, {"ok": False, "detail": "invalid universe id"})
                return
            from pipeline.ship import ship

            result = ship(universe, web_public=PUBLIC)
            self._send_json(200 if result["ok"] else 500, result)
            return
        self._send_json(404, {"ok": False, "detail": "unknown endpoint"})

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            return body if isinstance(body, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}

    # -- plumbing ---------------------------------------------------------------

    def _send(self, status: int, ctype: str, body: bytes, cache: str = "no-cache") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(payload).encode())


def _schedule_refreshes() -> None:
    """Kick off the boot refresh in the BACKGROUND. The port must bind
    immediately — platform health checks gate deploys on /healthz, and a
    slow or rate-limited source must never wedge a deploy. Until the first
    refresh lands, the artifacts already on disk serve (the previous run's,
    or the committed demo set on a fresh clone)."""
    minutes = float(os.environ.get("BRIEF_REFRESH_MINUTES", "30"))

    def boot() -> None:
        print("[serve] boot refresh:", refresh_artifacts()["detail"], file=sys.stderr)

    if os.environ.get("BRIEF_REFRESH_ON_BOOT", "1") != "0":
        threading.Thread(target=boot, name="boot-refresh", daemon=True).start()
    if minutes <= 0:
        return

    def loop() -> None:
        while True:
            time.sleep(minutes * 60)
            print("[serve] scheduled refresh:", refresh_artifacts()["detail"], file=sys.stderr)

    threading.Thread(target=loop, name="brief-refresh", daemon=True).start()


def _default_to_real_providers() -> None:
    """The deployed product pulls real data unless told otherwise.

    This is the only place a default can reach every deploy path: blueprint
    deploys get env vars from render.yaml, but manually-created services
    ignore that file entirely and would otherwise boot on fixtures. Explicit
    BRIEF_* env vars always win (BRIEF_PROVIDERS=fixture is the demo mode).
    Email transport has no real vendor yet, so it defaults to fixtures —
    ship writes the .html to out/emails/ instead of failing.
    """
    os.environ.setdefault("BRIEF_PROVIDERS", "real")
    os.environ.setdefault("BRIEF_EMAIL", "fixture")


def main() -> int:
    os.chdir(REPO)  # relative paths (universes/, out/emails) resolve from the repo
    _default_to_real_providers()
    port = int(os.environ.get("PORT", "8000"))
    ensure_web_built()  # no-op when the deploy build already produced dist/
    _schedule_refreshes()
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[serve] pre-market read on :{port} (dist={DIST.exists()})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
