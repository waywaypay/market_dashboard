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
not a CDN. Provider selection still comes from the BRIEF_* env vars.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "web" / "dist"
PUBLIC = REPO / "web" / "public"
ARTIFACT_PREFIXES = ("/brief.json", "/briefs/", "/universes.json")

_refresh_lock = threading.Lock()

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
                self._send(
                    503,
                    "text/plain; charset=utf-8",
                    b"Dashboard not built yet - run `npm run build` in web/ "
                    b"(the deploy build command does this).",
                )
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


def main() -> int:
    os.chdir(REPO)  # relative paths (universes/, out/emails) resolve from the repo
    port = int(os.environ.get("PORT", "8000"))
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
