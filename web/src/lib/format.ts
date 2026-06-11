/** Formatting helpers. All numerics render in IBM Plex Mono via the `num` class. */

export function fmtPct(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

export function fmtPrice(v: number): string {
  return v.toFixed(2);
}

export function fmtRvol(v: number | null | undefined): string {
  return v == null ? "—" : `${v.toFixed(1)}×`;
}

/** "6:58a PT" style timestamps, rendered in the universe's delivery tz. */
export function fmtClock(iso: string, tz: string): string {
  const d = new Date(iso);
  const time = new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: tz,
  })
    .format(d)
    .toLowerCase()
    .replace(" am", "a")
    .replace(" pm", "p");
  const zone =
    new Intl.DateTimeFormat("en-US", { timeZoneName: "short", timeZone: tz })
      .formatToParts(d)
      .find((p) => p.type === "timeZoneName")?.value ?? "";
  return `${time} ${zone.replace("S", "").replace("D", "")}`.trim();
}

export function fmtDate(iso: string, tz: string): string {
  return new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: tz,
  }).format(new Date(iso));
}

/** Live countdown to the open: "opens in 42:17" / "1:02:09" / "market open". */
export function fmtCountdown(openIso: string, nowMs: number): string {
  const delta = Math.floor((new Date(openIso).getTime() - nowMs) / 1000);
  if (delta <= 0) return "market open";
  const h = Math.floor(delta / 3600);
  const m = Math.floor((delta % 3600) / 60);
  const s = delta % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `opens in ${h}:${mm}:${ss}` : `opens in ${mm}:${ss}`;
}

export function minutesAgo(iso: string, nowMs: number): number {
  return Math.max(0, Math.floor((nowMs - new Date(iso).getTime()) / 60000));
}

export function fmtVolume(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${Math.round(v / 1_000)}K`;
  return String(v);
}
