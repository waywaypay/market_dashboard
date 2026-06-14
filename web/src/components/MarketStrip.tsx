/** Market strip: peer tiles, subject pinned + accented, in config order.
 * Hovering a flagged tile highlights its attributed priority signal via
 * driver_item_id — the product's visible point of view. */
import { useMemo } from "react";
import type { DailyBrief, Quote } from "../lib/contracts";
import { fmtPct, fmtPrice } from "../lib/format";
import { SectionHead } from "./bits";

export function MarketStrip({
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
  const quotes = useMemo(() => {
    const subject = brief.market.find((q) => q.ticker === brief.subject_ticker);
    const rest = brief.market.filter((q) => q.ticker !== brief.subject_ticker);
    return subject ? [subject, ...rest] : rest; // subject pinned, peers in config order
  }, [brief]);

  return (
    <section aria-label="Market strip" className="mx-auto max-w-[1400px] px-4 pt-6 sm:px-6">
      <SectionHead title="Market" />
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
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function Tile({
  quote,
  isSubject,
  highlighted,
  onHover,
}: {
  quote: Quote;
  isSubject: boolean;
  highlighted: boolean;
  onHover: (ticker: string | null, driverItemId: string | null) => void;
}) {
  const dir = quote.chg_pct >= 0 ? "text-up" : "text-down";
  const enter = () => onHover(quote.ticker, quote.driver_item_id ?? null);
  const leave = () => onHover(null, null);

  return (
    <li className="min-w-[148px] flex-1 sm:min-w-0">
      <div
        tabIndex={0}
        onMouseEnter={enter}
        onMouseLeave={leave}
        onFocus={enter}
        onBlur={leave}
        aria-label={`${quote.ticker} ${fmtPct(quote.chg_pct)}${
          quote.flagged ? ", unusual move" : ""
        }${quote.driver_item_id ? ", linked to a priority signal" : ""}`}
        className={`group h-full cursor-default rounded-md border bg-card p-2.5 shadow-tile transition-all ${
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
        {quote.driver_item_id && (
          <div className="num mt-1 flex justify-end text-[11px]">
            <span className={highlighted ? "text-accent" : "text-faint"}>↳ signal</span>
          </div>
        )}
      </div>
    </li>
  );
}
