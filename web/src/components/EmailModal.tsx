/** "First Read" email preview modal. Renders the same three sections as the
 * shipped email (subject-company news → comp-by-comp → sector headlines) from
 * the same artifact, then hands the actual send to the pipeline via /api/ship
 * so dashboard and email can never disagree. */
import { useEffect, useRef, useState } from "react";
import type { DailyBrief, Item } from "../lib/contracts";
import { shipFirstRead } from "../lib/loadBrief";
import { fmtClock, fmtDate, fmtPct, fmtRvol } from "../lib/format";
import { categoryColor, UP, DOWN } from "../lib/colors";

export function EmailModal({ brief, onClose }: { brief: DailyBrief; onClose: () => void }) {
  const [shipping, setShipping] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const ship = async () => {
    setShipping(true);
    setResult(await shipFirstRead(brief.universe_id));
    setShipping(false);
  };

  const subjectRows = brief.by_company[brief.subject_name] ?? [];
  const compRows = Object.entries(brief.by_company).filter(
    ([company]) => company !== brief.subject_name,
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/50 p-4 sm:p-8"
      role="dialog"
      aria-modal="true"
      aria-label="First Read email preview"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-[640px] rounded-md bg-card shadow-lift">
        <div className="flex items-center justify-between border-b border-hairline px-4 py-3">
          <div>
            <h2 className="font-display text-[14px] font-semibold text-ink">
              First Read — {brief.universe_label}
            </h2>
            <p className="num text-[11px] text-muted">
              {fmtDate(brief.generated_at, brief.display_tz)} · renders from the same
              artifact as the cockpit
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close preview"
            className="rounded-sm border border-hairline px-2 py-1 text-[12px] text-muted hover:text-ink"
          >
            Esc ✕
          </button>
        </div>

        {/* the email body, previewed */}
        <div className="max-h-[60vh] overflow-y-auto bg-surface p-4">
          <div className="mx-auto max-w-[600px] border border-hairline bg-card">
            <div className="bg-ink p-4">
              <div className="text-[10px] font-bold uppercase tracking-[0.2em] text-accent">
                ▦ First Read — {brief.universe_label}
              </div>
              <div className="num mt-1 text-[11px] text-faint">
                {brief.counts.total_items} items · {brief.counts.hot_items} hot
              </div>
              <p className="mt-2.5 font-display text-[15px] leading-snug text-white">
                {brief.tldr}
              </p>
            </div>
            <EmailSection
              title={`${brief.subject_name} (${brief.subject_ticker})`}
              items={subjectRows}
              brief={brief}
              emptyLine={`Nothing material on ${brief.subject_name} overnight.`}
            />
            {compRows.map(([company, items]) => (
              <EmailSection key={company} title={company} items={items} brief={brief} compact />
            ))}
            <EmailSection title="Sector" items={brief.sector_headlines} brief={brief} />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 border-t border-hairline px-4 py-3">
          <button
            type="button"
            onClick={ship}
            disabled={shipping}
            className="rounded-md bg-accent px-3 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-[#0B6362] disabled:opacity-60"
          >
            {shipping ? "Shipping…" : "Ship it →"}
          </button>
          <p className="min-w-0 flex-1 text-[11px] leading-snug text-muted" aria-live="polite">
            {result
              ? result.ok
                ? `Shipped — ${result.detail}`
                : `Not shipped: ${result.detail}`
              : "Ships via the pipeline's EmailProvider — the fixture provider writes the .html to out/emails/."}
          </p>
        </div>
      </div>
    </div>
  );
}

function EmailSection({
  title,
  items,
  brief,
  compact = false,
  emptyLine,
}: {
  title: string;
  items: Item[];
  brief: DailyBrief;
  compact?: boolean;
  emptyLine?: string;
}) {
  if (items.length === 0 && !emptyLine) return null;
  return (
    <div className="px-4 pt-4 last:pb-4">
      <div
        className={`border-b-2 border-ink pb-1 text-[10px] font-bold uppercase tracking-[0.16em] ${
          compact ? "text-muted" : "text-ink"
        }`}
      >
        {title}
      </div>
      {items.length === 0 ? (
        <p className="py-2.5 text-[12px] text-muted">{emptyLine}</p>
      ) : (
        items.map((item) => (
          <div key={item.id} className="border-b border-hairline py-2.5 last:border-b-0">
            <div className="flex flex-wrap items-center gap-x-1.5 text-[10px] text-muted">
              <span
                className="font-bold uppercase tracking-wider"
                style={{ color: categoryColor(item.category, brief.categories) }}
              >
                {item.category}
              </span>
              <span>·</span>
              <span className="num">{fmtClock(item.ts, brief.display_tz)}</span>
              <span>·</span>
              <span>{item.source}</span>
              {item.price_reaction && (
                <>
                  <span>·</span>
                  <span
                    className="num"
                    style={{ color: item.price_reaction.chg_pct >= 0 ? UP : DOWN }}
                  >
                    {fmtPct(item.price_reaction.chg_pct)} · {fmtRvol(item.price_reaction.rvol)} vol
                    {item.price_reaction.flagged ? " ⚑" : ""}
                  </span>
                </>
              )}
            </div>
            <p className="mt-1 text-[13px] leading-relaxed text-ink">
              <strong>
                {item.company ?? "Sector"}
                {item.ticker ? ` (${item.ticker})` : ""}:
              </strong>{" "}
              {item.summary}{" "}
              <a
                href={item.url}
                target="_blank"
                rel="noreferrer"
                className="whitespace-nowrap text-[11px] text-accent hover:underline"
              >
                ↗ source
              </a>
            </p>
          </div>
        ))
      )}
    </div>
  );
}
