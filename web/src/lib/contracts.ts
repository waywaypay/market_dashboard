/**
 * Zod schemas mirroring pipeline/contracts/models.py — DailyBrief and friends.
 * The dashboard validates the artifact on load, so a malformed brief.json fails
 * loudly in the UI's own voice rather than rendering garbage. Keep this in sync
 * with the Pydantic contract; it is the web side of the same interface.
 */
import { z } from "zod";

export const sourceKind = z.enum(["rss", "edgar", "news", "quotes"]);

export const quoteSchema = z.object({
  ticker: z.string(),
  name: z.string(),
  last: z.number(),
  chg_pct: z.number(),
  volume: z.number(),
  avg_volume: z.number(),
  sigma: z.number(),
  rvol: z.number().nullable().optional(),
  flagged: z.boolean().default(false),
  flag_reason: z.string().nullable().optional(),
  driver_item_id: z.string().nullable().optional(),
});

export const priceReactionSchema = z.object({
  ticker: z.string(),
  chg_pct: z.number(),
  rvol: z.number().nullable().optional(),
  flagged: z.boolean().default(false),
});

export const itemSchema = z.object({
  id: z.string(),
  ticker: z.string().nullable().optional(),
  company: z.string().nullable().optional(),
  category: z.string(),
  materiality: z.number().int().min(1).max(5),
  summary: z.string(),
  title: z.string(),
  url: z.string(),
  source: z.string(),
  ts: z.string(), // ISO 8601
  is_subject_relevant: z.boolean(),
  price_reaction: priceReactionSchema.nullable().optional(),
  is_driver: z.boolean().default(false),
});

export const sourceHealthSchema = z.object({
  provider: sourceKind,
  status: z.enum(["ok", "stale", "failed"]),
  last_ts: z.string().nullable().optional(),
  detail: z.string().nullable().optional(),
});

export const countsSchema = z.object({
  total_items: z.number().int(),
  hot_items: z.number().int(),
});

export const pricePointSchema = z.object({
  d: z.string(), // ISO date YYYY-MM-DD
  c: z.number(), // close
});

export const dailyBriefSchema = z.object({
  universe_id: z.string(),
  generated_at: z.string(),
  market_open_at: z.string(),
  tldr: z.string(),
  counts: countsSchema,
  market: z.array(quoteSchema),
  priority_signals: z.array(itemSchema),
  by_company: z.record(z.string(), z.array(itemSchema)),
  sector_headlines: z.array(itemSchema),
  source_status: z.array(sourceHealthSchema),
  universe_label: z.string(),
  subject_ticker: z.string(),
  subject_name: z.string(),
  categories: z.array(z.string()),
  display_tz: z.string(),
  classifier_engine: z.string(),
  // provenance: artifacts predating these fields read as fixture — the
  // banner must never give synthetic data the benefit of the doubt
  data_mode: z.enum(["real", "fixture", "mixed"]).default("fixture"),
  provider_modes: z.record(z.string(), z.string()).default({}),
  // historical daily closes per ticker for the overlay chart; best-effort, so
  // older/degraded artifacts (no history source) simply default to empty
  history: z.record(z.string(), z.array(pricePointSchema)).default({}),
  // "Today's First Read": the narrative morning note (VeniceAI when keyed, else
  // a deterministic composer). Optional + defaulted so older artifacts validate;
  // empty means no note this run and the UI/email fall back to `tldr`.
  first_read: z.string().default(""),
  first_read_engine: z.string().default("none"),
});

export const universeEntrySchema = z.object({
  id: z.string(),
  label: z.string(),
  subject_ticker: z.string(),
  subject_name: z.string(),
});

export type Quote = z.infer<typeof quoteSchema>;
export type PricePoint = z.infer<typeof pricePointSchema>;
export type PriceReaction = z.infer<typeof priceReactionSchema>;
export type Item = z.infer<typeof itemSchema>;
export type SourceHealth = z.infer<typeof sourceHealthSchema>;
export type DailyBrief = z.infer<typeof dailyBriefSchema>;
export type UniverseEntry = z.infer<typeof universeEntrySchema>;
