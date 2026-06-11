/** Sector headlines: broader items not tied to a single comp. */
import type { DailyBrief, Item } from "../lib/contracts";
import { fmtClock } from "../lib/format";
import { CategoryChip, EmptyState, MaterialityDots, SectionHead } from "./bits";

export function SectorHeadlines({
  brief,
  visible,
}: {
  brief: DailyBrief;
  visible: (item: Item) => boolean;
}) {
  const items = brief.sector_headlines.filter(visible);
  return (
    <section aria-label="Sector headlines" className="mx-auto max-w-[1400px] px-4 py-7 sm:px-6">
      <SectionHead title="Sector" hint={<span className="num">{items.length} headlines</span>} />
      {items.length === 0 ? (
        <EmptyState>No sector-wide stories survived the cut this morning.</EmptyState>
      ) : (
        <ul className="grid gap-2 md:grid-cols-2">
          {items.map((item) => (
            <li key={item.id}>
              <article className="h-full rounded-md border border-hairline bg-card p-3 shadow-tile">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
                  <CategoryChip category={item.category} categories={brief.categories} />
                  <MaterialityDots value={item.materiality} />
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
              </article>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
