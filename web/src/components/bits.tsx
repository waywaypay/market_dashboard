/** Small shared presentational pieces: category chips, price badges, the mark. */
import { categoryColor, UP, DOWN } from "../lib/colors";
import type { PriceReaction } from "../lib/contracts";
import { fmtPct, fmtRvol } from "../lib/format";

export function Mark({ className = "" }: { className?: string }) {
  // Four-quad mark; one quad in the brand teal. Deliberate, not decorative.
  return (
    <svg viewBox="0 0 16 16" className={`h-4 w-4 ${className}`} aria-hidden="true">
      <rect x="0" y="0" width="7" height="7" fill="currentColor" opacity="0.35" />
      <rect x="9" y="0" width="7" height="7" fill="currentColor" opacity="0.6" />
      <rect x="0" y="9" width="7" height="7" fill="currentColor" opacity="0.6" />
      <rect x="9" y="9" width="7" height="7" fill="#0E7C7B" />
    </svg>
  );
}

export function CategoryChip({
  category,
  categories,
}: {
  category: string;
  categories: string[];
}) {
  const color = categoryColor(category, categories);
  return (
    <span
      className="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider"
      style={{ color, backgroundColor: `${color}14` }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: color }} />
      {category}
    </span>
  );
}

export function PriceBadge({ reaction }: { reaction: PriceReaction }) {
  const color = reaction.chg_pct >= 0 ? UP : DOWN;
  return (
    <span
      className="num inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[11px] font-medium"
      style={{ color, borderColor: `${color}33`, backgroundColor: `${color}0D` }}
      title={`Price reaction: ${reaction.ticker} ${fmtPct(reaction.chg_pct)} on ${fmtRvol(
        reaction.rvol,
      )} relative volume${reaction.flagged ? " — flagged unusual" : ""}`}
    >
      {reaction.flagged && <span aria-hidden="true">⚑</span>}
      {reaction.ticker} {fmtPct(reaction.chg_pct)} · {fmtRvol(reaction.rvol)} vol
    </span>
  );
}

export function MaterialityDots({ value }: { value: number }) {
  return (
    <span
      className="inline-flex items-center gap-0.5"
      title={`Materiality ${value}/5`}
      aria-label={`Materiality ${value} of 5`}
    >
      {[1, 2, 3, 4, 5].map((n) => (
        <span
          key={n}
          className={`h-1 w-1 rounded-full ${n <= value ? "bg-ink" : "bg-hairline"}`}
        />
      ))}
    </span>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-hairline bg-card px-4 py-6 text-sm text-muted">
      {children}
    </div>
  );
}

export function SectionHead({
  title,
  hint,
}: {
  title: string;
  hint?: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex items-baseline justify-between border-b-2 border-ink pb-1.5">
      <h2 className="font-display text-[13px] font-semibold uppercase tracking-[0.14em] text-ink">
        {title}
      </h2>
      {hint && <div className="text-[11px] text-muted">{hint}</div>}
    </div>
  );
}
