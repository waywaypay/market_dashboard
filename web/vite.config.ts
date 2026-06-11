import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const here = dirname(fileURLToPath(import.meta.url));

/**
 * Dev-only middleware that backs the dashboard's "ship First Read" button.
 *
 * Local-first: there is no hosted backend. The button POSTs /api/ship, and
 * this handler shells out to the pipeline's ship entrypoint, which re-renders
 * the email from the SAME artifact the dashboard reads and "sends" it via the
 * configured EmailProvider (the FixtureProvider writes .html to out/emails/).
 * Keeps dashboard and email in lockstep with zero extra infrastructure.
 */
function shipEmailPlugin(): Plugin {
  const repoRoot = resolve(here, "..");
  const venvPy = resolve(repoRoot, ".venv/bin/python");
  const python = existsSync(venvPy) ? venvPy : "python3";

  return {
    name: "ship-email",
    configureServer(server) {
      server.middlewares.use("/api/ship", (req, res) => {
        if (req.method !== "POST") {
          res.statusCode = 405;
          res.end(JSON.stringify({ ok: false, detail: "POST only" }));
          return;
        }
        let body = "";
        req.on("data", (c) => (body += c));
        req.on("end", () => {
          let universe = "";
          try {
            universe = JSON.parse(body || "{}").universe ?? "";
          } catch {
            /* ignore — validated below */
          }
          if (!/^[a-z0-9_-]+$/i.test(universe)) {
            res.statusCode = 400;
            res.end(JSON.stringify({ ok: false, detail: "invalid universe id" }));
            return;
          }
          const proc = spawn(python, ["-m", "pipeline.ship", "--universe", universe], {
            cwd: repoRoot,
          });
          let out = "";
          let err = "";
          proc.stdout.on("data", (d) => (out += d));
          proc.stderr.on("data", (d) => (err += d));
          proc.on("close", (code) => {
            res.setHeader("Content-Type", "application/json");
            const lastLine = out.trim().split("\n").pop() || "";
            try {
              const parsed = JSON.parse(lastLine);
              res.statusCode = parsed.ok ? 200 : 500;
              res.end(JSON.stringify(parsed));
            } catch {
              res.statusCode = 500;
              res.end(
                JSON.stringify({
                  ok: false,
                  detail: err.trim() || `ship exited ${code}`,
                }),
              );
            }
          });
        });
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), shipEmailPlugin()],
  server: { port: 5173, strictPort: false },
});
