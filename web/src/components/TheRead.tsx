/** "The Read" — full-width band under the header: the tldr line in Space
 * Grotesk plus the item counts. This is the 60-second answer. */
import type { DailyBrief } from "../lib/contracts";

export function TheRead({ brief }: { brief: DailyBrief }) {
  const flagged = brief.market.filter((q) => q.flagged).length;
  return (
    <section
      aria-label="The read"
      className="border-b border-hairline bg-card"
    >
      <div className="mx-auto max-w-[1400px] px-4 py-5 sm:px-6">
        <p className="font-display text-[19px] font-medium leading-snug text-ink sm:text-[22px]">
          {brief.tldr}
        </p>
        <p className="num mt-2 text-[12px] text-muted">
          {brief.counts.total_items} items · {brief.counts.hot_items} hot ·{" "}
          {flagged} unusual {flagged === 1 ? "move" : "moves"}
        </p>
      </div>
    </section>
  );
}
