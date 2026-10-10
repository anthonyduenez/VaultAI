import type { Box } from "../api/types";

interface BoxStyle {
  left: string;
  top: string;
  width: string;
  height: string;
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
// Rounding hides float noise such as 0.4 * 100 = 40.00000000000001.
const pct = (v: number) => `${Number((v * 100).toFixed(3))}%`;

/** Normalized (x0, y0, x1, y1) box, origin top-left, → absolute-position percentages. */
export function bboxToStyle(bbox: Box): BoxStyle {
  const [a, b, c, d] = [clamp01(bbox[0]), clamp01(bbox[1]), clamp01(bbox[2]), clamp01(bbox[3])];
  const x0 = Math.min(a, c);
  const y0 = Math.min(b, d);
  return { left: pct(x0), top: pct(y0), width: pct(Math.max(a, c) - x0), height: pct(Math.max(b, d) - y0) };
}

export interface Insets {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

const NO_INSETS: Insets = { left: 0, top: 0, right: 0, bottom: 0 };

export function insetsToCrop(insets: Insets): Box | null {
  const { left, top, right, bottom } = insets;
  if (left === 0 && top === 0 && right === 0 && bottom === 0) return null;
  return [left, top, 1 - right, 1 - bottom];
}

export function cropToInsets(crop: Box | null): Insets {
  if (!crop) return NO_INSETS;
  const [x0, y0, x1, y1] = crop;
  return { left: x0, top: y0, right: 1 - x1, bottom: 1 - y1 };
}
