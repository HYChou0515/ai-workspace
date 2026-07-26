/**
 * Colour for a single-select value (#GH-projects B) — GitHub-Projects-style
 * coloured chips for status / select fields. A value gets a stable hue from the
 * categorical palette (`--cat-1..7`), auto-assigned by a small deterministic
 * hash so the same value always looks the same; a schema `colors:` map can pin a
 * value to a named hue instead. Empty ⇒ the neutral slot.
 */

import type { EntityFieldSpec } from "../../api/entities";

const SLOTS = 7; // --cat-1 .. --cat-7 (7 = neutral)

export type ChipColor = { bg: string; fg: string };

/** 1-based palette slot from a value's hash (never the neutral slot 7). */
function hashSlot(value: string): number {
  let h = 0;
  for (let i = 0; i < value.length; i++) h = (h * 31 + value.charCodeAt(i)) >>> 0;
  return (h % (SLOTS - 1)) + 1; // 1..6
}

const NAMED: Record<string, number> = {
  green: 1, blue: 2, amber: 3, yellow: 3, violet: 4, purple: 4,
  teal: 5, cyan: 5, rose: 6, red: 6, slate: 7, gray: 7, grey: 7, neutral: 7,
};

/** Resolve a schema override (a hue name like "green", or a slot like "3"/"cat-3")
 * to a palette slot; unknown → neutral. */
function slotOf(name: string): number {
  const key = name.trim().toLowerCase();
  if (NAMED[key]) return NAMED[key];
  const m = /(\d)/.exec(key);
  const n = m ? Number(m[1]) : 0;
  return n >= 1 && n <= SLOTS ? n : SLOTS;
}

export function selectColor(value: string, fieldSpec?: EntityFieldSpec): ChipColor {
  if (!value) return pair(SLOTS);
  const override = fieldSpec?.colors?.[value];
  return pair(override ? slotOf(override) : hashSlot(value));
}

// Literal token strings (one per slot) so the no-undefined-tokens guard sees each
// full, defined token — building the name by string interpolation would scan as a
// bare, undefined reference.
const PAIRS: ChipColor[] = [
  { bg: "var(--cat-1-bg)", fg: "var(--cat-1-fg)" },
  { bg: "var(--cat-2-bg)", fg: "var(--cat-2-fg)" },
  { bg: "var(--cat-3-bg)", fg: "var(--cat-3-fg)" },
  { bg: "var(--cat-4-bg)", fg: "var(--cat-4-fg)" },
  { bg: "var(--cat-5-bg)", fg: "var(--cat-5-fg)" },
  { bg: "var(--cat-6-bg)", fg: "var(--cat-6-fg)" },
  { bg: "var(--cat-7-bg)", fg: "var(--cat-7-fg)" },
];
const pair = (n: number): ChipColor => PAIRS[n - 1] ?? PAIRS[SLOTS - 1];
