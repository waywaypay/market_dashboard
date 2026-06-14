/** Loud provenance banner. Renders whenever the brief was NOT generated
 * entirely from real providers — synthetic demo data must never pass for
 * live market data, no matter which deploy path served the artifact. */
import type { DailyBrief } from "../lib/contracts";

const SOURCE_LABELS: Record<string, string> = {
  rss: "RSS",
  edgar: "EDGAR",
  news: "News",
  quotes: "Market quotes",
};

export function DataModeBanner({ brief }: { brief: DailyBrief }) {
  if (brief.data_mode === "real") return null;

  const fixtureSources = Object.keys(SOURCE_LABELS)
    .filter((s) => (brief.provider_modes[s] ?? "fixture") === "fixture")
    .map((s) => SOURCE_LABELS[s]);

  const headline =
    brief.data_mode === "fixture"
      ? "SYNTHETIC DEMO DATA"
      : "PARTIALLY SYNTHETIC DATA";
  const body =
    brief.data_mode === "fixture"
      ? "Every story and price below is a canned fixture — nothing here is live market data."
      : `These sources are serving canned fixtures, not live data: ${fixtureSources.join(", ")}.`;

  return (
    <div
      role="alert"
      className="border-b-2 border-[#B42318] bg-[#FEF3F2] px-4 py-2.5 sm:px-6"
    >
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="rounded-sm bg-[#B42318] px-1.5 py-0.5 text-[11px] font-bold tracking-wider text-white">
          ⚠ {headline}
        </span>
        <span className="text-[13px] font-medium text-[#7A271A]">{body}</span>
        <span className="text-[12px] text-[#B42318]/80">
          Run with BRIEF_PROVIDERS=real (and check the Sources panel) for live pulls.
        </span>
      </div>
    </div>
  );
}
