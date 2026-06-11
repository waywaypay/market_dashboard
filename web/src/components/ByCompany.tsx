/** By company: collapsible card per company, subject first (artifact order),
 * items grouped and category-chipped. */
import { useState } from "react";
import type { DailyBrief, Item } from "../lib/contracts";
import { fmtClock } from "../lib/format";
import { CategoryChip, EmptyState, MaterialityDots, PriceBadge, SectionHead } from "./bits";

export function ByCompany({
  brief,
  visible,
}: {
  brief: DailyBrief;
  visible: (item: Item) => boolean;
}) {
  const companies = Object.entries(brief.by_company)
    .map(([company, items]) => [company, items.filter(visible)] as const)
    .filter(([, items]) => items.length > 0);

  return (
    <section aria-label="By company" className="mx-auto max-w-[1400px] px-4 pt-7 sm:px-6">
      <SectionHead title="By company" hint={<span className="num">{companies.length} names</span>} />
      {companies.length === 0 ? (
        <EmptyState>
          No company-tagged items at the current filters — loosen the
          materiality slider or re-enable categories in the rail.
        </EmptyState>
      ) : (
        <div className="space-y-2">
          {companies.map(([company, items]) => (
            <CompanyCard
              key={company}
              company={company}
              items={items}
              brief={brief}
              isSubject={company === brief.subject_name}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function CompanyCard({
  company,
  items,
  brief,
  isSubject,
}: {
  company: string;
  items: Item[];
  brief: DailyBrief;
  isSubject: boolean;
}) {
  const [open, setOpen] = useState(isSubject); // subject expanded by default
  const ticker = items[0]?.ticker;
  const quote = brief.market.find((q) => q.ticker === ticker);

  return (
    <div
      className={`rounded-md border bg-card shadow-tile ${
        isSubject ? "border-l-[3px] border-l-accent border-y-hairline border-r-hairline" : "border-hairline"
      }`}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
      >
        <span className={`text-[11px] transition-transform motion-safe:duration-150 ${open ? "rotate-90" : ""}`} aria-hidden="true">
          ▸
        </span>
        <span className="text-[14px] font-semibold text-ink">{company}</span>
        {ticker && <span className="num text-[12px] text-muted">{ticker}</span>}
        {isSubject && (
          <span className="text-[9px] font-semibold uppercase tracking-wider text-accent">
            subject
          </span>
        )}
        {quote && (
          <span className={`num text-[12px] ${quote.chg_pct >= 0 ? "text-up" : "text-down"}`}>
            {quote.chg_pct >= 0 ? "+" : ""}
            {quote.chg_pct.toFixed(1)}%{quote.flagged ? " ⚑" : ""}
          </span>
        )}
        <span className="num ml-auto text-[11px] text-muted">
          {items.length} item{items.length === 1 ? "" : "s"}
        </span>
      </button>
      {open && (
        <ul className="border-t border-hairline">
          {items.map((item) => (
            <li key={item.id} className="border-b border-hairline px-3 py-2.5 last:border-b-0">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
                <CategoryChip category={item.category} categories={brief.categories} />
                <MaterialityDots value={item.materiality} />
                {item.is_subject_relevant && !isSubject && (
                  <span
                    className="text-[10px] font-medium uppercase tracking-wider text-accent"
                    title={`Relevant to ${brief.subject_ticker}`}
                  >
                    {brief.subject_ticker}-relevant
                  </span>
                )}
                <span className="num ml-auto text-muted">
                  {fmtClock(item.ts, brief.display_tz)}
                </span>
              </div>
              <p className="mt-1.5 text-[13px] leading-relaxed text-ink">
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
              {item.price_reaction?.flagged && (
                <div className="mt-1.5">
                  <PriceBadge reaction={item.price_reaction} />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
