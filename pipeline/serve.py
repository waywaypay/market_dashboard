"""Production server — the deploy entrypoint (Render/Fly/any host with a PORT).

    python -m pipeline.serve

One process does everything the local `make dev` split does:
  * serves the built dashboard from web/dist (immutable-cached assets)
  * serves artifacts (brief.json, briefs/*, universes.json) from web/public
    with no-store headers, so a pipeline re-run updates the cockpit without
    a redeploy
  * POST /api/ship     -> re-render + send the First Read for a universe
  * POST /api/refresh  -> re-run the pipeline now (the dashboard's ↻ button)
  * GET  /healthz      -> 200 for platform health checks
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

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "web" / "dist"
PUBLIC = REPO / "web" / "public"
ARTIFACT_PREFIXES = ("/brief.json", "/briefs/", "/universes.json")

_refresh_lock = threading.Lock()

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
    the server must stay up even when a scheduled run fails."""
    from pipeline.contracts.universe import discover_universes, load_universe
    from pipeline.orchestrator import run_universe

    if not _refresh_lock.acquire(blocking=False):
        return {"ok": False, "detail": "a refresh is already running"}
    try:
        now = datetime.now(timezone.utc)
        ids = []
        for n, path in enumerate(discover_universes(REPO / "universes")):
            universe = load_universe(path)
            run_universe(
                universe,
                now=now,
                web_public=PUBLIC,
                default=(n == 0),
                send_email=False,  # email leaves only via the explicit ship action
            )
            ids.append(universe.id)
        return {"ok": True, "detail": f"refreshed {', '.join(ids)} at {now.isoformat()}"}
    except Exception as exc:
        traceback.print_exc()
        return {"ok": False, "detail": f"refresh failed: {type(exc).__name__}: {exc}"}
    finally:
        _refresh_lock.release()


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
        target = resolve_static(self.path)
        if target is None:
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
        if self.path == "/api/ship":
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                universe = str(payload.get("universe", ""))
            except json.JSONDecodeError:
                universe = ""
            if not universe.replace("-", "").replace("_", "").isalnum():
                self._send_json(400, {"ok": False, "detail": "invalid universe id"})
                return
            from pipeline.ship import ship

            result = ship(universe, web_public=PUBLIC)
            self._send_json(200 if result["ok"] else 500, result)
            return
        self._send_json(404, {"ok": False, "detail": "unknown endpoint"})

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
    minutes = float(os.environ.get("BRIEF_REFRESH_MINUTES", "30"))
    if os.environ.get("BRIEF_REFRESH_ON_BOOT", "1") != "0":
        print("[serve] boot refresh:", refresh_artifacts()["detail"], file=sys.stderr)
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
