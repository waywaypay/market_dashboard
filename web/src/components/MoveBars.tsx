/** Horizontal bar chart of each peer's pre-market move, ordered by the size of
 * the move — biggest movers on top, smallest at the bottom. Diverging from a
 * center zero line: gains run right (green), losses run left (red), so direction
 * and magnitude read at once. A companion to the tiles above that answers
 * "who's moving, and how much" in a single glance. Plain divs sized by percent,
 * no chart dependency — consistent with the rest of the app. Hovering (or
 * focusing) a row highlights its attributed priority signal, mirroring the
 * tiles. */
import { useMemo } from "react";
import type { DailyBrief } from "../lib/contracts";
import { fmtPct } from "../lib/format";
import { DOWN, UP } from "../lib/colors";

export function MoveBars({
  brief,
  hoverTicker,
  hoverItemId,
  onHover,
}: {
  brief: DailyBrief;
  hoverTicker: string | null;
  hoverItemId: string | null;
  onHover: (ticker: string | null, driverItemId: string | null) => void;
}) {
  // largest move to smallest, by magnitude — direction is carried by the bar
  const rows = useMemo(
    () => [...brief.market].sort((a, b) => Math.abs(b.chg_pct) - Math.abs(a.chg_pct)),
    [brief.market],
  );
  // scale every bar to the biggest absolute move; floor at 1% so a flat morning
  // doesn't stretch a 0.1% wiggle across the whole track
  const maxAbs = useMemo(
    () => Math.max(1, ...rows.map((q) => Math.abs(q.chg_pct))),
    [rows],
  );

  if (rows.length === 0) return null;

  return (
    <div className="mt-4 rounded-md border border-hairline bg-card p-3 shadow-tile">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
          Price move by ticker
        </span>
        <span className="num text-[10px] text-faint">largest → smallest</span>
      </div>
      <ul className="flex flex-col gap-1">
        {rows.map((q) => {
          const isSubject = q.ticker === brief.subject_ticker;
          const highlighted =
            hoverTicker === q.ticker ||
            (hoverItemId != null && q.driver_item_id === hoverItemId);
          const up = q.chg_pct >= 0;
          const color = up ? UP : DOWN;
          const w = (Math.abs(q.chg_pct) / maxAbs) * 50; // ≤ 50% of the track per side
          const enter = () => onHover(q.ticker, q.driver_item_id ?? null);
          const leave = () => onHover(null, null);
          return (
            <li key={q.ticker}>
              <div
                tabIndex={0}
                onMouseEnter={enter}
                onMouseLeave={leave}
                onFocus={enter}
                onBlur={leave}
                aria-label={`${q.ticker} ${fmtPct(q.chg_pct)}${isSubject ? ", subject" : ""}${
                  q.driver_item_id ? ", linked to a priority signal" : ""
                }`}
                className={`group grid cursor-default grid-cols-[3.5rem_1fr_3.5rem] items-center gap-2 rounded-sm px-1 py-0.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 ${
                  highlighted ? "bg-accent/10" : "hover:bg-ink/5"
                }`}
              >
                <span
                  className={`num truncate text-right text-[11px] font-semibold ${
                    isSubject ? "text-accent" : "text-ink"
                  }`}
                >
                  {q.ticker}
                </span>

                <div className="relative h-4">
                  {/* zero baseline */}
                  <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-hairline" />
                  <div
                    className="absolute top-1/2 h-2.5 -translate-y-1/2 rounded-[2px] transition-[width]"
                    style={{
                      width: `${w}%`,
                      left: up ? "50%" : undefined,
                      right: up ? undefined : "50%",
                      backgroundColor: color,
                      opacity: highlighted ? 1 : 0.82,
                    }}
                  />
                </div>

                <span className="num text-right text-[11px] font-medium" style={{ color }}>
                  {fmtPct(q.chg_pct)}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
