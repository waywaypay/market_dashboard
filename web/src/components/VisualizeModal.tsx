/** Modal host for the historical price overlay. Same shell conventions as the
 * First Read modal (escape / backdrop close, focus on open). The chart renders
 * from the artifact's `history` map, so it needs no extra fetch. */
import { useEffect, useRef } from "react";
import type { DailyBrief } from "../lib/contracts";
import { fmtDate } from "../lib/format";
import { PriceChart } from "./PriceChart";

export function VisualizeModal({ brief, onClose }: { brief: DailyBrief; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/50 p-4 sm:p-8"
      role="dialog"
      aria-modal="true"
      aria-label="Historical price chart"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-[980px] rounded-md bg-card shadow-lift">
        <div className="flex items-center justify-between border-b border-hairline px-4 py-3">
          <div>
            <h2 className="font-display text-[14px] font-semibold text-ink">
              Price history — {brief.universe_label}
            </h2>
            <p className="num text-[11px] text-muted">
              % change over ~3 months · as of {fmtDate(brief.generated_at, brief.display_tz)} ·
              normalized to each name's window start
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close chart"
            className="rounded-sm border border-hairline px-2 py-1 text-[12px] text-muted hover:text-ink"
          >
            Esc ✕
          </button>
        </div>

        <div className="p-4 sm:p-5">
          <PriceChart brief={brief} />
        </div>
      </div>
    </div>
  );
}
