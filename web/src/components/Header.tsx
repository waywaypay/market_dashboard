/** Dark ink header band: mark, product name, date, live open-countdown,
 * universe selector (config swap), manual refresh. */
import { useEffect, useState } from "react";
import type { DailyBrief, UniverseEntry } from "../lib/contracts";
import { fmtCountdown, fmtDate } from "../lib/format";
import { Mark } from "./bits";

export function Header({
  brief,
  universes,
  onSelectUniverse,
  onRefresh,
  refreshing,
}: {
  brief: DailyBrief;
  universes: UniverseEntry[];
  onSelectUniverse: (id: string) => void;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const countdown = fmtCountdown(brief.market_open_at, nowMs);
  const isOpen = countdown === "market open";

  return (
    <header className="bg-ink text-white">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-4 sm:px-6">
        <div className="flex items-center gap-2.5">
          <Mark className="text-white" />
          <div>
            <div className="font-display text-[15px] font-semibold leading-tight tracking-wide">
              Pre-Market Read
            </div>
            <div className="text-[11px] leading-tight text-faint">
              {fmtDate(brief.generated_at, brief.display_tz)}
            </div>
          </div>
        </div>

        <div
          className={`num flex items-center gap-2 rounded-sm border px-2.5 py-1 text-[12px] ${
            isOpen
              ? "border-accent/60 text-accent"
              : "border-white/15 text-white/90"
          }`}
          role="timer"
          aria-live="off"
          title={`Market open: ${new Date(brief.market_open_at).toLocaleString()}`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              isOpen ? "bg-accent" : "bg-accent motion-safe:animate-pulsering"
            }`}
            aria-hidden="true"
          />
          {countdown}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <label htmlFor="universe" className="text-[11px] uppercase tracking-wider text-faint">
            Universe
          </label>
          <select
            id="universe"
            className="rounded-sm border border-white/20 bg-ink px-2 py-1.5 text-[13px] text-white"
            value={brief.universe_id}
            onChange={(e) => onSelectUniverse(e.target.value)}
          >
            {(universes.length
              ? universes
              : [
                  {
                    id: brief.universe_id,
                    label: brief.universe_label,
                    subject_ticker: brief.subject_ticker,
                    subject_name: brief.subject_name,
                  },
                ]
            ).map((u) => (
              <option key={u.id} value={u.id}>
                {u.label} · {u.subject_ticker}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="rounded-sm border border-white/20 px-2.5 py-1.5 text-[12px] text-white/90 transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
            aria-label="Refresh artifact"
            title="Reload brief.json"
          >
            {refreshing ? "Reloading…" : "↻ Refresh"}
          </button>
        </div>
      </div>
    </header>
  );
}
