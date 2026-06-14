/** Market strip: peer tiles, subject pinned + accented. Sortable by %chg /
 * RVOL / ticker. Hovering a flagged tile highlights its attributed priority
 * signal via driver_item_id — the product's visible point of view. */
import { useMemo, useState } from "react";
import type { DailyBrief, Quote } from "../lib/contracts";
import { fmtPct, fmtPrice, fmtRvol } from "../lib/format";
import { SectionHead } from "./bits";

type SortKey = "config" | "chg" | "rvol" | "ticker";

export function MarketStrip({
  brief,
  hoverTicker,
  hoverItemId,
  onHover,
  onVisualize,
  watchlistActive,
  inWatchlist,
  onToggleMember,
}: {
  brief: DailyBrief;
  hoverTicker: string | null;
  hoverItemId: string | null;
  onHover: (ticker: string | null, driverItemId: string | null) => void;
  onVisualize: () => void;
  watchlistActive: boolean;
  inWatchlist: (ticker: string) => boolean;
  onToggleMember: (ticker: string) => void;
}) {
  const [sort, setSort] = useState<SortKey>("config");
  const hasHistory = Object.keys(brief.history).length > 0;

  const quotes = useMemo(() => {
    const pool = watchlistActive
      ? brief.market.filter((q) => inWatchlist(q.ticker))
      : brief.market;
    const subject = pool.find((q) => q.ticker === brief.subject_ticker);
    const rest = pool.filter((q) => q.ticker !== brief.subject_ticker);
    const sorted = [...rest];
    if (sort === "chg") sorted.sort((a, b) => Math.abs(b.chg_pct) - Math.abs(a.chg_pct));
    if (sort === "rvol") sorted.sort((a, b) => (b.rvol ?? 0) - (a.rvol ?? 0));
    if (sort === "ticker") sorted.sort((a, b) => a.ticker.localeCompare(b.ticker));
    return subject ? [subject, ...sorted] : sorted; // subject always pinned
  }, [brief, sort, watchlistActive, inWatchlist]);

  return (
    <section aria-label="Market strip" className="mx-auto max-w-[1400px] px-4 pt-6 sm:px-6">
      <SectionHead
        title="Market"
        hint={
          <div className="flex items-center gap-2">
            {hasHistory && (
              <button
                type="button"
                onClick={onVisualize}
                className="flex items-center gap-1 rounded-sm border border-hairline px-1.5 py-0.5 font-medium text-ink transition-colors hover:border-accent hover:text-accent"
                title="Overlay 3-month price history for the peer set"
              >
                <span aria-hidden="true">📈</span> Visualize
              </button>
            )}
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
          {watchlistActive
            ? "No names in this watchlist are in today's market — add some from Manage watchlists."
            : "No quotes this run — the market provider returned nothing."}
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
              showStar={watchlistActive}
              onToggleMember={onToggleMember}
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
  showStar,
  onToggleMember,
}: {
  quote: Quote;
  isSubject: boolean;
  highlighted: boolean;
  onHover: (ticker: string | null, driverItemId: string | null) => void;
  showStar: boolean;
  onToggleMember: (ticker: string) => void;
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
        aria-label={`${quote.ticker} ${fmtPct(quote.chg_pct)} on ${fmtRvol(quote.rvol)} relative volume${
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
          <div className="flex items-center gap-1">
            {quote.flagged && (
              <span
                className={`num text-[11px] ${dir}`}
                title={`Unusual move (${quote.flag_reason}); hover to see the attributed signal`}
              >
                ⚑
              </span>
            )}
            {showStar && (
              <button
                type="button"
                onClick={() => onToggleMember(quote.ticker)}
                aria-label={`Remove ${quote.ticker} from this watchlist`}
                title="Remove from this watchlist"
                className="text-[12px] leading-none text-accent transition-colors hover:text-down"
              >
                ★
              </button>
            )}
          </div>
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
