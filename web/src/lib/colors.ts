/**
 * Category colors are positional: the artifact's `categories` array order maps
 * onto the four-color palette, so any universe taxonomy (Clinical/Commercial/…
 * or Product/Partnership/…) re-skins from config alone.
 */
const PALETTE = ["#7C3AED", "#0891B2", "#D97706", "#2563EB"];

export function categoryColor(category: string, categories: string[]): string {
  const idx = categories.indexOf(category);
  return PALETTE[(idx >= 0 ? idx : categories.length) % PALETTE.length];
}

export const ACCENT = "#0E7C7B";
export const UP = "#1A7F4B";
export const DOWN = "#B42318";
export const INK = "#12161C";
export const HAIRLINE = "#E4E7EC";
