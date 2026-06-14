/** Create a custom universe: name it, give a subject ticker + peer tickers, and
 * the server writes a universe config and runs the pipeline once so its brief
 * exists. Also lists your custom universes so you can delete them. Same modal
 * shell conventions as the other modals (Esc / backdrop close, focus on open).
 *
 * Custom universes pull real data for their tickers (keyless quotes + EDGAR;
 * news needs EXA_API_KEY). They live on the server, so a free-tier spin-down or
 * redeploy clears them — by design, the product keeps no database. */
import { useEffect, useMemo, useRef, useState } from "react";
import type { UniverseEntry } from "../lib/contracts";
import { createUniverse, deleteUniverse } from "../lib/loadBrief";

function parseTickers(raw: string): string[] {
  const seen = new Set<string>();
  for (const t of raw.split(/[\s,]+/)) {
    const up = t.trim().toUpperCase();
    if (up) seen.add(up);
  }
  return [...seen];
}

export function NewUniverseModal({
  universes,
  onClose,
  onCreated,
  onDeleted,
}: {
  universes: UniverseEntry[];
  onClose: () => void;
  onCreated: (id: string) => void | Promise<void>;
  onDeleted: (id: string) => void | Promise<void>;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const [label, setLabel] = useState("");
  const [subjectTicker, setSubjectTicker] = useState("");
  const [subjectName, setSubjectName] = useState("");
  const [peers, setPeers] = useState("");
  const [keywords, setKeywords] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, busy]);

  const customs = useMemo(() => universes.filter((u) => u.custom), [universes]);
  const tickerCount = useMemo(() => {
    const all = new Set(parseTickers(peers));
    const subj = subjectTicker.trim().toUpperCase();
    if (subj) all.add(subj);
    return all.size;
  }, [peers, subjectTicker]);

  const canSubmit = label.trim().length > 0 && subjectTicker.trim().length > 0 && !busy;

  const submit = async () => {
    setError("");
    setBusy(true);
    const res = await createUniverse({
      label: label.trim(),
      subject_ticker: subjectTicker.trim().toUpperCase(),
      subject_name: subjectName.trim() || undefined,
      peer_tickers: parseTickers(peers),
      sector_keywords: parseTickers(keywords).map((k) => k.toLowerCase()),
    });
    setBusy(false);
    if (res.ok && res.id) {
      await onCreated(res.id);
    } else {
      setError(res.detail || "Could not create the universe.");
    }
  };

  const remove = async (id: string, name: string) => {
    if (!window.confirm(`Delete the “${name}” universe? This can't be undone.`)) return;
    setBusy(true);
    const res = await deleteUniverse(id);
    setBusy(false);
    if (res.ok) await onDeleted(id);
    else setError(res.detail || "Could not delete the universe.");
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/50 p-4 sm:p-8"
      role="dialog"
      aria-modal="true"
      aria-label="Create a custom universe"
      onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}
    >
      <div className="w-full max-w-[560px] rounded-md bg-card shadow-lift">
        <div className="flex items-center justify-between border-b border-hairline px-4 py-3">
          <div>
            <h2 className="font-display text-[14px] font-semibold text-ink">
              New universe
            </h2>
            <p className="text-[11px] text-muted">
              A peer set of your own. The server pulls real quotes + filings for these tickers.
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
            className="rounded-sm border border-hairline px-2 py-1 text-[12px] text-muted hover:text-ink disabled:opacity-50"
          >
            Esc ✕
          </button>
        </div>

        <div className="max-h-[64vh] space-y-3 overflow-y-auto p-4">
          <Field label="Name" hint="e.g. “Mega-cap tech”">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              maxLength={60}
              placeholder="My universe"
              className="w-full rounded-sm border border-hairline px-2 py-1.5 text-[13px] text-ink focus:border-accent focus:outline-none"
            />
          </Field>

          <div className="grid grid-cols-[1fr_1.4fr] gap-2">
            <Field label="Subject ticker" hint="the pinned name">
              <input
                value={subjectTicker}
                onChange={(e) => setSubjectTicker(e.target.value.toUpperCase())}
                placeholder="AAPL"
                className="num w-full rounded-sm border border-hairline px-2 py-1.5 text-[13px] text-ink focus:border-accent focus:outline-none"
              />
            </Field>
            <Field label="Subject name" hint="optional">
              <input
                value={subjectName}
                onChange={(e) => setSubjectName(e.target.value)}
                placeholder="Apple"
                className="w-full rounded-sm border border-hairline px-2 py-1.5 text-[13px] text-ink focus:border-accent focus:outline-none"
              />
            </Field>
          </div>

          <Field label="Peer tickers" hint="space- or comma-separated">
            <textarea
              value={peers}
              onChange={(e) => setPeers(e.target.value)}
              rows={2}
              placeholder="MSFT, GOOGL, AMZN, META, NVDA"
              className="num w-full resize-y rounded-sm border border-hairline px-2 py-1.5 text-[13px] text-ink focus:border-accent focus:outline-none"
            />
          </Field>

          <Field label="Sector keywords" hint="optional — sharpens news search">
            <input
              value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
              placeholder="cloud, semiconductors, AI"
              className="w-full rounded-sm border border-hairline px-2 py-1.5 text-[13px] text-ink focus:border-accent focus:outline-none"
            />
          </Field>

          <p className="text-[11px] text-muted">
            <span className="num text-ink">{tickerCount}</span> ticker{tickerCount === 1 ? "" : "s"}{" "}
            (max 40). Building runs the pipeline once — it can take up to a minute.
          </p>

          {error && (
            <p className="rounded-sm border border-down/30 bg-down/5 px-2 py-1.5 text-[12px] text-down">
              {error}
            </p>
          )}

          {customs.length > 0 && (
            <div className="border-t border-hairline pt-3">
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
                Your custom universes
              </h3>
              <ul className="space-y-1.5">
                {customs.map((u) => (
                  <li
                    key={u.id}
                    className="flex items-center gap-2 rounded-md border border-hairline px-2 py-1.5"
                  >
                    <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">
                      {u.label} <span className="num text-[11px] text-muted">· {u.subject_ticker}</span>
                    </span>
                    <button
                      type="button"
                      onClick={() => remove(u.id, u.label)}
                      disabled={busy}
                      aria-label={`Delete ${u.label}`}
                      className="shrink-0 rounded-sm border border-hairline px-1.5 py-0.5 text-[11px] text-muted transition-colors hover:border-down hover:text-down disabled:opacity-50"
                    >
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3 border-t border-hairline px-4 py-3">
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="rounded-md bg-accent px-3 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-[#0B6362] disabled:opacity-50"
          >
            {busy ? "Building…" : "Create universe →"}
          </button>
          <p className="min-w-0 flex-1 text-[11px] leading-snug text-muted">
            Saved on the server; a free-tier spin-down or redeploy clears custom universes (no DB by
            design).
          </p>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-muted">
        {label}
        {hint && <span className="ml-1 normal-case tracking-normal text-faint">· {hint}</span>}
      </span>
      {children}
    </label>
  );
}
