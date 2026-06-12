/**
 * Artifact loading. The dashboard is a read-only consumer of the pipeline's
 * output: /universes.json (selector manifest) + /briefs/<id>.json (per
 * universe), with /brief.json as the default fallback. The UI ships no data
 * of its own.
 */
import {
  dailyBriefSchema,
  universeEntrySchema,
  type DailyBrief,
  type UniverseEntry,
} from "./contracts";
import { z } from "zod";

async function fetchJson(url: string): Promise<unknown> {
  const res = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) {
    // serve.py answers a missing artifact with 503 + the refresh status
    // (running/failed + why) — surface that instead of a bare status code
    let detail = "";
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body — fall through to the generic message */
    }
    throw new Error(detail || `${url}: HTTP ${res.status}`);
  }
  return res.json();
}

export async function loadUniverses(): Promise<UniverseEntry[]> {
  try {
    const data = await fetchJson("/universes.json");
    return z.array(universeEntrySchema).parse(data);
  } catch {
    return []; // selector degrades to the default artifact only
  }
}

export async function loadBrief(universeId?: string): Promise<DailyBrief> {
  const url = universeId ? `/briefs/${universeId}.json` : "/brief.json";
  const data = await fetchJson(url);
  return dailyBriefSchema.parse(data);
}

/** Ask the server to re-run the pipeline (serve.py and the vite dev plugin
 * both expose this). Failure is non-fatal — the caller refetches the artifact
 * either way, so a static host degrades to a plain reload. */
export async function refreshPipeline(): Promise<void> {
  try {
    await fetch("/api/refresh", { method: "POST" });
  } catch {
    /* static hosting: no pipeline endpoint — refetch alone is the refresh */
  }
}

export async function shipFirstRead(
  universeId: string,
): Promise<{ ok: boolean; detail: string }> {
  try {
    const res = await fetch("/api/ship", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ universe: universeId }),
    });
    const body = await res.json();
    return { ok: Boolean(body.ok), detail: String(body.detail ?? "") };
  } catch (err) {
    return {
      ok: false,
      detail:
        "Ship endpoint unreachable — run the dashboard via `make dev` so the dev server can hand off to the pipeline.",
    };
  }
}
