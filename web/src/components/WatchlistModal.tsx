/** Watchlist manager. Create / rename / delete personal watchlists and pick
 * which of the universe's names belong to each. Same modal shell conventions
 * as the First Read / Visualize modals (escape + backdrop close, focus on
 * open). Watchlists are saved in this browser only — the cockpit reads them as
 * a focus filter, the pipeline never sees them. */
import { useEffect, useRef } from "react";
import type { DailyBrief } from "../lib/contracts";
import type { Watchlist } from "../lib/watchlists";

export function WatchlistModal({
  brief,
  watchlists,
  activeId,
  onClose,
  onCreate,
  onRename,
  onDelete,
  onSetActive,
  onToggleTicker,
  onSetMembers,
}: {
  brief: DailyBrief;
  watchlists: Watchlist[];
  activeId: string | null;
  onClose: () => void;
  onCreate: () => void;
  onRename: (id: string, name: string) => void;
  onDelete: (id: string) => void;
  onSetActive: (id: string | null) => void;
  onToggleTicker: (id: string, ticker: string) => void;
  onSetMembers: (id: string, tickers: string[]) => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // The universe roster the cockpit actually shows (subject pinned first).
  const roster = brief.market.map((q) => ({ ticker: q.ticker, name: q.name }));
  const active = watchlists.find((w) => w.id === activeId) ?? null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/50 p-4 sm:p-8"
      role="dialog"
      aria-modal="true"
      aria-label="Manage watchlists"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-[560px] rounded-md bg-card shadow-lift">
        <div className="flex items-center justify-between border-b border-hairline px-4 py-3">
          <div>
            <h2 className="font-display text-[14px] font-semibold text-ink">
              Watchlists — {brief.universe_label}
            </h2>
            <p className="text-[11px] text-muted">
              A saved subset of these names. Pick one to focus the cockpit on it.
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close watchlists"
            className="rounded-sm border border-hairline px-2 py-1 text-[12px] text-muted hover:text-ink"
          >
            Esc ✕
          </button>
        </div>

        <div className="max-h-[64vh] space-y-4 overflow-y-auto p-4">
          {/* the saved lists */}
          <section aria-label="Your watchlists" className="space-y-1.5">
            {watchlists.length === 0 ? (
              <p className="text-[12px] text-muted">
                No watchlists yet. Create one, then tick the names to include.
              </p>
            ) : (
              watchlists.map((w) => {
                const isActive = w.id === activeId;
                return (
                  <div
                    key={w.id}
                    className={`flex items-center gap-2 rounded-md border px-2 py-1.5 ${
                      isActive ? "border-accent bg-accent/5" : "border-hairline"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => onSetActive(isActive ? null : w.id)}
                      aria-pressed={isActive}
                      title={isActive ? "Editing — click to deselect" : "Select to edit its names"}
                      className={`h-3.5 w-3.5 shrink-0 rounded-full border ${
                        isActive ? "border-accent bg-accent" : "border-muted"
                      }`}
                      aria-label={`Edit ${w.name}`}
                    />
                    <input
                      value={w.name}
                      onChange={(e) => onRename(w.id, e.target.value)}
                      onFocus={() => !isActive && onSetActive(w.id)}
                      aria-label="Watchlist name"
                      className="min-w-0 flex-1 rounded-sm border border-transparent bg-transparent px-1 py-0.5 text-[13px] font-medium text-ink hover:border-hairline focus:border-accent focus:outline-none"
                    />
                    <span className="num shrink-0 text-[11px] text-muted">
                      {w.tickers.length} name{w.tickers.length === 1 ? "" : "s"}
                    </span>
                    <button
                      type="button"
                      onClick={() => onDelete(w.id)}
                      aria-label={`Delete ${w.name}`}
                      className="shrink-0 rounded-sm border border-hairline px-1.5 py-0.5 text-[11px] text-muted transition-colors hover:border-down hover:text-down"
                    >
                      Delete
                    </button>
                  </div>
                );
              })
            )}
            <button
              type="button"
              onClick={onCreate}
              className="mt-1 rounded-sm border border-dashed border-accent/50 px-2.5 py-1 text-[12px] font-medium text-accent transition-colors hover:bg-accent/5"
            >
              ＋ New watchlist
            </button>
          </section>

          {/* the name picker for the selected list */}
          {active && (
            <section aria-label={`Names in ${active.name}`} className="border-t border-hairline pt-3">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
                  Names in “{active.name || "Untitled"}”
                </h3>
                <div className="flex items-center gap-2 text-[11px]">
                  <button
                    type="button"
                    onClick={() => onSetMembers(active.id, roster.map((r) => r.ticker))}
                    className="text-accent hover:underline disabled:text-muted disabled:no-underline"
                    disabled={active.tickers.length === roster.length}
                  >
                    Select all
                  </button>
                  <span className="text-faint">·</span>
                  <button
                    type="button"
                    onClick={() => onSetMembers(active.id, [])}
                    className="text-accent hover:underline disabled:text-muted disabled:no-underline"
                    disabled={active.tickers.length === 0}
                  >
                    Clear
                  </button>
                </div>
              </div>
              {roster.length === 0 ? (
                <p className="text-[12px] text-muted">No names in today's brief to choose from.</p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {roster.map(({ ticker, name }) => {
                    const member = active.tickers.includes(ticker);
                    const isSubject = ticker === brief.subject_ticker;
                    return (
                      <button
                        key={ticker}
                        type="button"
                        onClick={() => onToggleTicker(active.id, ticker)}
                        aria-pressed={member}
                        title={name}
                        className={`num rounded-sm border px-2 py-1 text-[11px] font-medium transition-colors ${
                          member
                            ? "border-accent bg-accent/10 text-accent"
                            : "border-hairline text-muted hover:border-accent/50 hover:text-ink"
                        }`}
                      >
                        {member ? "★" : "☆"} {ticker}
                        {isSubject && <span className="ml-1 text-[9px] uppercase">subj</span>}
                      </button>
                    );
                  })}
                </div>
              )}
            </section>
          )}
        </div>

        <div className="border-t border-hairline px-4 py-2.5">
          <p className="text-[11px] leading-snug text-muted">
            Saved in this browser only. Choose a watchlist in the rail to focus the cockpit on
            its names; pick “All names” to clear the focus.
          </p>
        </div>
      </div>
    </div>
  );
}
