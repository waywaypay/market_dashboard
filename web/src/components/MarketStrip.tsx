/** Market strip: peer tiles, subject pinned + accented. Sortable by %chg /
 * RVOL / ticker. Hovering a flagged tile highlights its attributed priority
 * signal via driver_item_id — the product's visible point of view. */
import { useMemo, useState } from "react";
import type { DailyBrief, Quote } from "../lib/contracts";
import { fmtPct, fmtPrice, fmtRvol } from "../lib/format";
import { SectionHead } from "./bits";
import { MoveBars } from "./MoveBars";

type SortKey = "config" | "chg" | "rvol" | "ticker";

export function MarketStrip({
  brief,
  hoverTicker,
  hoverItemId,
  onHover,
  onVisualize,
}: {
  brief: DailyBrief;
  hoverTicker: string | null;
  hoverItemId: string | null;
  onHover: (ticker: string | null, driverItemId: string | null) => void;
  onVisualize: (ticker?: string) => void;
}) {
  const [sort, setSort] = useState<SortKey>("config");

  const quotes = useMemo(() => {
    const subject = brief.market.find((q) => q.ticker === brief.subject_ticker);
    const rest = brief.market.filter((q) => q.ticker !== brief.subject_ticker);
    const sorted = [...rest];
    if (sort === "chg") sorted.sort((a, b) => Math.abs(b.chg_pct) - Math.abs(a.chg_pct));
    if (sort === "rvol") sorted.sort((a, b) => (b.rvol ?? 0) - (a.rvol ?? 0));
    if (sort === "ticker") sorted.sort((a, b) => a.ticker.localeCompare(b.ticker));
    return subject ? [subject, ...sorted] : sorted; // subject always pinned
  }, [brief, sort]);

  return (
    <section aria-label="Market strip" className="mx-auto max-w-[1400px] px-4 pt-6 sm:px-6">
      <SectionHead
        title="Market"
        hint={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onVisualize()}
              className="flex items-center gap-1 rounded-sm border border-hairline px-1.5 py-0.5 font-medium text-ink transition-colors hover:border-accent hover:text-accent"
              title="Overlay 3-month price history for the peer set (or click a tile)"
            >
              <span aria-hidden="true">📈</span> Visualize
            </button>
            <div className="flex items-center gap-1" role="group" aria-label="Sort tiles">
              <span className="mr-1 hidden sm:inline">sort</span>
              {(
                [
                  ["config", "peer set"],
                  ["chg", "%chg"],
                  ["rvol", "RVOL"],
                  ["ticker", "A–Z"],
                ] as [SortKey, string][]
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setSort(key)}
                  aria-pressed={sort === key}
                  className={`rounded-sm px-1.5 py-0.5 transition-colors ${
                    sort === key
                      ? "bg-ink text-white"
                      : "text-muted hover:text-ink"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        }
      />
      {quotes.length === 0 ? (
        <p className="text-sm text-muted">
          No quotes this run — the market provider returned nothing.
        </p>
      ) : (
        <ul className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-4 sm:overflow-visible sm:px-0 lg:grid-cols-6">
          {quotes.map((q) => (
            <Tile
              key={q.ticker}
              quote={q}
              isSubject={q.ticker === brief.subject_ticker}
              highlighted={
                hoverTicker === q.ticker ||
                (hoverItemId != null && q.driver_item_id === hoverItemId)
              }
              onHover={onHover}
              onVisualize={onVisualize}
            />
          ))}
        </ul>
      )}
      {quotes.length > 0 && (
        <MoveBars
          brief={brief}
          hoverTicker={hoverTicker}
          hoverItemId={hoverItemId}
          onHover={onHover}
          onVisualize={onVisualize}
        />
      )}
    </section>
  );
}

function Tile({
  quote,
  isSubject,
  highlighted,
  onHover,
  onVisualize,
}: {
  quote: Quote;
  isSubject: boolean;
  highlighted: boolean;
  onHover: (ticker: string | null, driverItemId: string | null) => void;
  onVisualize: (ticker?: string) => void;
}) {
  const dir = quote.chg_pct >= 0 ? "text-up" : "text-down";
  const enter = () => onHover(quote.ticker, quote.driver_item_id ?? null);
  const leave = () => onHover(null, null);
  const open = () => onVisualize(quote.ticker);

  return (
    <li className="min-w-[148px] flex-1 sm:min-w-0">
      <div
        role="button"
        tabIndex={0}
        onMouseEnter={enter}
        onMouseLeave={leave}
        onFocus={enter}
        onBlur={leave}
        onClick={open}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            open();
          }
        }}
        aria-label={`${quote.ticker} ${fmtPct(quote.chg_pct)} on ${fmtRvol(quote.rvol)} relative volume${
          quote.flagged ? ", unusual move" : ""
        }${quote.driver_item_id ? ", linked to a priority signal" : ""} — chart price history`}
        className={`group h-full cursor-pointer rounded-md border bg-card p-2.5 shadow-tile transition-all hover:border-accent hover:shadow-lift focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 ${
          highlighted
            ? "border-accent ring-2 ring-accent/35 shadow-lift"
            : "border-hairline"
        } ${isSubject ? "border-l-[3px] border-l-accent" : ""}`}
      >
        <div className="flex items-baseline justify-between gap-2">
          <span className="num text-[13px] font-semibold text-ink">
            {quote.ticker}
            {isSubject && (
              <span className="ml-1 align-middle text-[9px] font-semibold uppercase tracking-wider text-accent">
                subject
              </span>
            )}
          </span>
          {quote.flagged && (
            <span
              className={`num text-[11px] ${dir}`}
              title={`Unusual move (${quote.flag_reason}); hover to see the attributed signal`}
            >
              ⚑
            </span>
          )}
        </div>
        <div className="mt-1.5 flex items-baseline justify-between gap-2">
          <span className="num text-[15px] text-ink">{fmtPrice(quote.last)}</span>
          <span className={`num text-[13px] font-medium ${dir}`}>{fmtPct(quote.chg_pct)}</span>
        </div>
        <div className="num mt-1 flex items-baseline justify-between text-[11px] text-muted">
          <span title="Relative volume vs average">{fmtRvol(quote.rvol)} vol</span>
          {quote.driver_item_id && (
            <span className={highlighted ? "text-accent" : "text-faint"}>↳ signal</span>
          )}
        </div>
      </div>
    </li>
  );
}
