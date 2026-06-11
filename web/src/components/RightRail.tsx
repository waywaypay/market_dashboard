/** Right rail: source-health panel, category filter chips, materiality
 * threshold slider, and the "Generate today's First Read →" action. On
 * mobile this renders above the main column. */
import type { DailyBrief } from "../lib/contracts";
import { categoryColor } from "../lib/colors";
import { fmtClock, minutesAgo } from "../lib/format";

const PROVIDER_LABELS: Record<string, string> = {
  rss: "RSS",
  edgar: "EDGAR",
  news: "News",
  quotes: "Market",
};

export function RightRail({
  brief,
  activeCategories,
  onToggleCategory,
  minMateriality,
  onMinMateriality,
  onGenerate,
}: {
  brief: DailyBrief;
  activeCategories: Set<string>;
  onToggleCategory: (category: string) => void;
  minMateriality: number;
  onMinMateriality: (v: number) => void;
  onGenerate: () => void;
}) {
  return (
    <aside aria-label="Controls and source health" className="space-y-4">
      <button
        type="button"
        onClick={onGenerate}
        className="w-full rounded-md bg-accent px-3 py-2.5 text-left text-[13px] font-semibold text-white shadow-tile transition-colors hover:bg-[#0B6362]"
      >
        Generate today's First Read →
      </button>

      <RailCard title="Filters">
        <div className="flex flex-wrap gap-1.5">
          {brief.categories.map((cat) => {
            const active = activeCategories.has(cat);
            const color = categoryColor(cat, brief.categories);
            return (
              <button
                key={cat}
                type="button"
                onClick={() => onToggleCategory(cat)}
                aria-pressed={active}
                className={`rounded-sm border px-2 py-1 text-[11px] font-medium transition-colors ${
                  active ? "" : "opacity-40"
                }`}
                style={{
                  color,
                  borderColor: `${color}55`,
                  backgroundColor: active ? `${color}14` : "transparent",
                }}
              >
                {cat}
              </button>
            );
          })}
        </div>
        <label className="mt-3 block text-[11px] font-medium uppercase tracking-wider text-muted">
          Materiality ≥ <span className="num text-ink">{minMateriality}</span>
          <input
            type="range"
            min={1}
            max={5}
            step={1}
            value={minMateriality}
            onChange={(e) => onMinMateriality(Number(e.target.value))}
            className="mt-1.5 w-full accent-[#0E7C7B]"
            aria-label="Minimum materiality"
          />
        </label>
      </RailCard>

      <RailCard title="Sources">
        <ul className="space-y-2">
          {brief.source_status.map((h) => (
            <SourceRow key={h.provider} brief={brief} {...h} />
          ))}
        </ul>
        <p className="mt-3 border-t border-hairline pt-2 text-[10px] leading-relaxed text-faint">
          Generated {fmtClock(brief.generated_at, brief.display_tz)} ·
          classifier: <span className="num">{brief.classifier_engine}</span>
        </p>
      </RailCard>
    </aside>
  );
}

function RailCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-hairline bg-card p-3 shadow-tile">
      <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
        {title}
      </h3>
      {children}
    </div>
  );
}

function SourceRow({
  brief,
  provider,
  status,
  last_ts,
  detail,
}: {
  brief: DailyBrief;
  provider: string;
  status: "ok" | "stale" | "failed";
  last_ts?: string | null;
  detail?: string | null;
}) {
  const nowMs = new Date(brief.generated_at).getTime();
  const label = PROVIDER_LABELS[provider] ?? provider;

  // Empty/failed states speak in the product's voice, not a generic spinner.
  let line: string;
  if (status === "ok" && last_ts) {
    line = `last pull ${fmtClock(last_ts, brief.display_tz)}`;
  } else if (status === "stale" && last_ts) {
    line = `No ${label} pulls since ${fmtClock(last_ts, brief.display_tz)} (${minutesAgo(
      last_ts,
      nowMs,
    )}m) — feed may be stale`;
  } else if (status === "stale") {
    line = `No ${label} pulls this run — feed may be stale`;
  } else {
    line = detail ? `${label} failed: ${detail}` : `${label} failed this run`;
  }

  const glyph = status === "ok" ? "✓" : status === "stale" ? "◷" : "✕";
  const tone =
    status === "ok" ? "text-accent" : status === "stale" ? "text-[#B54708]" : "text-down";

  return (
    <li className="flex items-start gap-2 text-[12px]">
      <span className={`num mt-px w-3 shrink-0 ${tone}`} aria-hidden="true">
        {glyph}
      </span>
      <div className="min-w-0">
        <div className="font-medium text-ink">
          {label}
          <span className={`ml-1.5 text-[10px] uppercase tracking-wider ${tone}`}>{status}</span>
        </div>
        <div className="text-[11px] leading-snug text-muted">{line}</div>
      </div>
    </li>
  );
}
