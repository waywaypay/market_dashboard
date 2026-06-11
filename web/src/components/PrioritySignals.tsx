/** Priority signals: material, price-linked items sorted by materiality.
 * Hovering a signal highlights its ticker's tile (and vice versa). */
import type { DailyBrief, Item } from "../lib/contracts";
import { fmtClock } from "../lib/format";
import {
  CategoryChip,
  EmptyState,
  MaterialityDots,
  PriceBadge,
  SectionHead,
} from "./bits";

export function PrioritySignals({
  brief,
  visible,
  hoverTicker,
  hoverItemId,
  onHover,
}: {
  brief: DailyBrief;
  visible: (item: Item) => boolean;
  hoverTicker: string | null;
  hoverItemId: string | null;
  onHover: (itemId: string | null, ticker: string | null) => void;
}) {
  const signals = brief.priority_signals.filter(visible);
  return (
    <section aria-label="Priority signals" className="mx-auto max-w-[1400px] px-4 pt-7 sm:px-6">
      <SectionHead
        title="Priority signals"
        hint={<span className="num">{signals.length} shown</span>}
      />
      {signals.length === 0 ? (
        <EmptyState>
          Nothing met the bar this morning — no high-materiality items and no
          attributed movers at the current filters.
        </EmptyState>
      ) : (
        <ul className="space-y-2">
          {signals.map((item) => (
            <Signal
              key={item.id}
              item={item}
              brief={brief}
              highlighted={
                hoverItemId === item.id ||
                (hoverTicker != null && item.ticker === hoverTicker && item.is_driver)
              }
              onHover={onHover}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function Signal({
  item,
  brief,
  highlighted,
  onHover,
}: {
  item: Item;
  brief: DailyBrief;
  highlighted: boolean;
  onHover: (itemId: string | null, ticker: string | null) => void;
}) {
  const enter = () => onHover(item.id, item.ticker ?? null);
  const leave = () => onHover(null, null);
  return (
    <li>
      <article
        tabIndex={0}
        onMouseEnter={enter}
        onMouseLeave={leave}
        onFocus={enter}
        onBlur={leave}
        className={`rounded-md border bg-card p-3 shadow-tile transition-all ${
          highlighted ? "border-accent ring-2 ring-accent/35 shadow-lift" : "border-hairline"
        }`}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[11px]">
          <span className="num rounded-sm bg-ink px-1.5 py-0.5 font-semibold text-white">
            {item.ticker ?? item.company ?? "SECTOR"}
          </span>
          <CategoryChip category={item.category} categories={brief.categories} />
          <MaterialityDots value={item.materiality} />
          {item.is_driver && (
            <span
              className={`rounded-sm border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider transition-colors ${
                highlighted
                  ? "border-accent bg-accent text-white"
                  : "border-accent/40 text-accent"
              }`}
              title="Attributed driver of an unusual move — hover to see the tile"
            >
              ↰ move driver
            </span>
          )}
          <span className="num ml-auto text-muted">{fmtClock(item.ts, brief.display_tz)}</span>
        </div>
        <p className="mt-2 text-[14px] leading-relaxed text-ink">
          {item.summary}{" "}
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            className="whitespace-nowrap text-[12px] text-accent hover:underline"
          >
            {item.source} ↗
          </a>
        </p>
        {item.price_reaction && (
          <div className="mt-2">
            <PriceBadge reaction={item.price_reaction} />
          </div>
        )}
      </article>
    </li>
  );
}
