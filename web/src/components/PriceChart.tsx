/** Multi-ticker historical price overlay. Each peer's daily closes are
 * normalized to % change from the start of the window, so names spanning very
 * different price levels are directly comparable on one axis. Subject is drawn
 * in the brand accent and pinned first; the legend toggles individual lines;
 * hovering reads every visible line at the nearest session. Pure SVG — no chart
 * dependency, consistent with the rest of the app. */
import { useMemo, useState } from "react";
import type { DailyBrief, PricePoint } from "../lib/contracts";
import { ACCENT, seriesColor } from "../lib/colors";

type Series = {
  ticker: string;
  name: string;
  isSubject: boolean;
  color: string;
  points: PricePoint[];
  pct: number[]; // % change from the first point, aligned to `points`
};

const W = 920;
const H = 420;
const M = { top: 16, right: 16, bottom: 28, left: 44 };
const INNER_W = W - M.left - M.right;
const INNER_H = H - M.top - M.bottom;

export function PriceChart({ brief }: { brief: DailyBrief }) {
  const series = useMemo(() => buildSeries(brief), [brief]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const dates = useMemo(() => {
    const set = new Set<string>();
    for (const s of series) for (const p of s.points) set.add(p.d);
    return [...set].sort();
  }, [series]);
  const dateIndex = useMemo(() => new Map(dates.map((d, i) => [d, i])), [dates]);

  const visible = series.filter((s) => !hidden.has(s.ticker));

  const [minY, maxY] = useMemo(() => {
    const vals = visible.flatMap((s) => s.pct);
    if (vals.length === 0) return [-1, 1];
    const lo = Math.min(...vals, 0);
    const hi = Math.max(...vals, 0);
    const pad = (hi - lo || 2) * 0.08;
    return [lo - pad, hi + pad];
  }, [visible]);

  if (series.length === 0) {
    return (
      <div className="flex h-[280px] items-center justify-center rounded-md border border-dashed border-hairline bg-card px-6 text-center text-sm text-muted">
        Historical price data isn't available for this run — the quote source
        in use doesn't supply daily history.
      </div>
    );
  }

  const n = dates.length;
  const x = (i: number) => M.left + (n <= 1 ? INNER_W / 2 : (i / (n - 1)) * INNER_W);
  const y = (p: number) => M.top + (1 - (p - minY) / (maxY - minY || 1)) * INNER_H;

  const yTicks = niceTicks(minY, maxY, 5);
  const xTickIdxs = pickXTicks(n);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W; // -> SVG user units
    const frac = (px - M.left) / INNER_W;
    const idx = Math.round(frac * (n - 1));
    setHoverIdx(idx >= 0 && idx < n ? idx : null);
  };

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Historical price, percent change from window start, by ticker"
        onMouseMove={onMove}
        onMouseLeave={() => setHoverIdx(null)}
      >
        {/* y gridlines + labels */}
        {yTicks.map((t) => (
          <g key={t}>
            <line
              x1={M.left}
              x2={W - M.right}
              y1={y(t)}
              y2={y(t)}
              stroke={t === 0 ? "#C4C9D2" : "#EEF0F3"}
              strokeWidth={t === 0 ? 1.25 : 1}
            />
            <text x={M.left - 6} y={y(t) + 3} textAnchor="end" className="fill-faint text-[10px]">
              {t > 0 ? "+" : ""}
              {t.toFixed(0)}%
            </text>
          </g>
        ))}

        {/* x date labels */}
        {xTickIdxs.map((i) => (
          <text
            key={i}
            x={x(i)}
            y={H - 8}
            textAnchor="middle"
            className="fill-faint text-[10px]"
          >
            {fmtDay(dates[i])}
          </text>
        ))}

        {/* hover crosshair */}
        {hoverIdx != null && (
          <line
            x1={x(hoverIdx)}
            x2={x(hoverIdx)}
            y1={M.top}
            y2={H - M.bottom}
            stroke="#9AA1AD"
            strokeWidth={1}
            strokeDasharray="3 3"
          />
        )}

        {/* lines (subject last so it sits on top) */}
        {[...visible].sort((a, b) => Number(a.isSubject) - Number(b.isSubject)).map((s) => (
          <polyline
            key={s.ticker}
            fill="none"
            stroke={s.color}
            strokeWidth={s.isSubject ? 2.5 : 1.5}
            strokeOpacity={s.isSubject ? 1 : 0.85}
            strokeLinejoin="round"
            strokeLinecap="round"
            points={s.points
              .map((p, i) => `${x(dateIndex.get(p.d) ?? 0)},${y(s.pct[i])}`)
              .join(" ")}
          />
        ))}

        {/* hover dots */}
        {hoverIdx != null &&
          visible.map((s) => {
            const at = s.points.findIndex((p) => dateIndex.get(p.d) === hoverIdx);
            if (at < 0) return null;
            return (
              <circle key={s.ticker} cx={x(hoverIdx)} cy={y(s.pct[at])} r={3} fill={s.color} />
            );
          })}
      </svg>

      {hoverIdx != null && <HoverCard dates={dates} idx={hoverIdx} series={visible} />}

      {/* legend / toggles */}
      <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1.5">
        {series.map((s) => {
          const off = hidden.has(s.ticker);
          const last = s.pct[s.pct.length - 1] ?? 0;
          return (
            <button
              key={s.ticker}
              type="button"
              onClick={() =>
                setHidden((prev) => {
                  const next = new Set(prev);
                  if (next.has(s.ticker)) next.delete(s.ticker);
                  else next.add(s.ticker);
                  return next;
                })
              }
              aria-pressed={!off}
              className={`flex items-center gap-1.5 rounded-sm px-1 py-0.5 text-[11px] transition-opacity ${
                off ? "opacity-35" : ""
              }`}
              title={`${s.name} — click to ${off ? "show" : "hide"}`}
            >
              <span
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: s.color }}
                aria-hidden="true"
              />
              <span className={`num font-semibold ${s.isSubject ? "text-ink" : "text-muted"}`}>
                {s.ticker}
              </span>
              <span className="num" style={{ color: last >= 0 ? "#1A7F4B" : "#B42318" }}>
                {last >= 0 ? "+" : ""}
                {last.toFixed(1)}%
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function HoverCard({
  dates,
  idx,
  series,
}: {
  dates: string[];
  idx: number;
  series: Series[];
}) {
  const rows = series
    .map((s) => {
      const at = s.points.findIndex((p) => p.d === dates[idx]);
      return at < 0 ? null : { ticker: s.ticker, color: s.color, pct: s.pct[at] };
    })
    .filter((r): r is { ticker: string; color: string; pct: number } => r != null)
    .sort((a, b) => b.pct - a.pct);
  if (rows.length === 0) return null;

  // float the card on whichever half keeps it on-screen
  const leftHalf = idx < dates.length / 2;
  const pos = leftHalf
    ? { left: `${(idx / Math.max(1, dates.length - 1)) * 100}%`, marginLeft: 12 }
    : { right: `${(1 - idx / Math.max(1, dates.length - 1)) * 100}%`, marginRight: 12 };

  return (
    <div
      className="pointer-events-none absolute top-2 z-10 rounded-md border border-hairline bg-card/95 px-2.5 py-1.5 shadow-lift backdrop-blur-sm"
      style={pos}
    >
      <div className="num mb-1 text-[10px] font-semibold text-muted">{fmtDay(dates[idx])}</div>
      <div className="grid grid-cols-[auto_auto] gap-x-2.5 gap-y-0.5">
        {rows.map((r) => (
          <div key={r.ticker} className="contents">
            <span className="num flex items-center gap-1 text-[11px] text-ink">
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ backgroundColor: r.color }}
              />
              {r.ticker}
            </span>
            <span
              className="num text-right text-[11px]"
              style={{ color: r.pct >= 0 ? "#1A7F4B" : "#B42318" }}
            >
              {r.pct >= 0 ? "+" : ""}
              {r.pct.toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function buildSeries(brief: DailyBrief): Series[] {
  const out: Series[] = [];
  let colorIdx = 0;
  for (const q of brief.market) {
    const points = brief.history[q.ticker];
    if (!points || points.length < 2) continue;
    const base = points[0].c;
    if (!base) continue;
    const isSubject = q.ticker === brief.subject_ticker;
    out.push({
      ticker: q.ticker,
      name: q.name,
      isSubject,
      color: isSubject ? ACCENT : seriesColor(colorIdx++),
      points,
      pct: points.map((p) => (p.c / base - 1) * 100),
    });
  }
  return out;
}

function niceTicks(min: number, max: number, count: number): number[] {
  const span = max - min || 1;
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const ticks: number[] = [];
  for (let t = Math.ceil(min / step) * step; t <= max + 1e-9; t += step) {
    ticks.push(Math.round(t * 100) / 100);
  }
  return ticks;
}

function pickXTicks(n: number): number[] {
  if (n <= 1) return [0];
  const count = Math.min(6, n);
  return Array.from({ length: count }, (_, k) => Math.round((k / (count - 1)) * (n - 1)));
}

function fmtDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}
