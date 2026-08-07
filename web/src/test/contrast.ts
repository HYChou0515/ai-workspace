/**
 * Contrast maths against the real `tokens.css`, shared by every a11y guard.
 *
 * Extracted from `styles/contrast.test.ts` when a second guard needed it
 * (the gantt bar, #690). Two copies of "composite this fill and take the
 * ratio" is how the two guards would end up disagreeing about what a token
 * is worth — the number has to come from one place.
 *
 * Everything here reads the STYLESHEET, not the DOM: the test environment does
 * not lay out or cascade, so `getComputedStyle` cannot be trusted for either
 * the resolved value of a custom property or which of two declarations won.
 */
import { readSrcFile } from "./readSrcFile";

export const TOKENS_CSS = readSrcFile("styles/tokens.css");

/** The light `:root` block and the dark `[data-theme="dark"]` override block. */
export const LIGHT = /:root\s*\{[\s\S]*?\n\s*\}/;
export const DARK = /\[data-theme="dark"\]\s*\{[\s\S]*?\n\s*\}/;

export type Rgba = [number, number, number, number];

function srgbToLinear(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function relLuminance(hex: string): number {
  const r = Number.parseInt(hex.slice(1, 3), 16);
  const g = Number.parseInt(hex.slice(3, 5), 16);
  const b = Number.parseInt(hex.slice(5, 7), 16);
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b);
}

/** WCAG 2.1 relative-contrast ratio between two opaque `#rrggbb` colours. */
export function contrast(a: string, b: string): number {
  const la = relLuminance(a);
  const lb = relLuminance(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/** The raw text of a custom property DECLARED in one block, or undefined. */
function declaredIn(css: string, block: RegExp, name: string): string | undefined {
  const m = block.exec(css);
  if (!m) throw new Error(`block ${block} not found`);
  return new RegExp(`${name}:\\s*([^;]+);`).exec(m[0])?.[1].trim();
}

/** The raw text of a custom property inside one block (`rgba(…)` / hex / `var(…)`). */
export function rawValueIn(css: string, block: RegExp, name: string): string {
  const v = declaredIn(css, block, name);
  if (v === undefined) throw new Error(`${name} not found in block`);
  return v;
}

/**
 * What a custom property resolves to under one theme.
 *
 * `[data-theme="dark"]` overrides only the tokens that need to flip; the rest
 * still cascade down from `:root`. A lookup that does not fall back reports a
 * token as missing when it is merely theme-invariant — `--info` and `--ink`
 * are declared once and used by both themes.
 */
function underTheme(css: string, block: RegExp, name: string): string {
  const own = declaredIn(css, block, name);
  if (own !== undefined) return own;
  const root = declaredIn(css, LIGHT, name);
  if (root === undefined) throw new Error(`${name} is declared in no block`);
  return root;
}

/**
 * Like `rawValueIn`, but follows one token aliasing another within the same
 * block. `--cat-7-bg` aliasing `--paper-2` is the reason this exists: the neutral
 * slot aliases a surface token instead of carrying its own hue, so a guard
 * that cannot dereference it simply skips the slot — which is how the neutral
 * slot stayed unmeasured.
 */
export function resolveValueIn(css: string, block: RegExp, name: string, depth = 0): string {
  const raw = underTheme(css, block, name);
  const alias = /^var\(\s*(--[\w-]+)\s*\)$/.exec(raw);
  if (!alias) return raw;
  if (depth > 8) throw new Error(`${name}: var() indirection does not terminate`);
  return resolveValueIn(css, block, alias[1], depth + 1);
}

/** A hex token's value inside one block, following `var()` aliases. */
export function tokenIn(css: string, block: RegExp, name: string): string {
  const v = resolveValueIn(css, block, name);
  const hit = /#[0-9A-Fa-f]{6}/.exec(v);
  if (!hit) throw new Error(`${name} is not a hex colour: ${v}`);
  return hit[0];
}

/** Parse `rgba(r,g,b,a)` / `rgb(r,g,b)` / `#rrggbb` into `[r,g,b,a]`. */
export function parseFill(v: string): Rgba {
  const rgba = /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)/.exec(v);
  if (rgba) {
    return [Number(rgba[1]), Number(rgba[2]), Number(rgba[3]), rgba[4] ? Number(rgba[4]) : 1];
  }
  const hex = /#([0-9A-Fa-f]{6})/.exec(v);
  if (!hex) throw new Error(`unparseable fill: ${v}`);
  const h = hex[1];
  return [
    Number.parseInt(h.slice(0, 2), 16),
    Number.parseInt(h.slice(2, 4), 16),
    Number.parseInt(h.slice(4, 6), 16),
    1,
  ];
}

/** Alpha-composite a fill over an opaque surface hex → a solid `#rrggbb`. */
export function over(fill: Rgba, surface: string): string {
  const [sr, sg, sb] = [
    Number.parseInt(surface.slice(1, 3), 16),
    Number.parseInt(surface.slice(3, 5), 16),
    Number.parseInt(surface.slice(5, 7), 16),
  ];
  const a = fill[3];
  const mix = (fg: number, bg: number) => Math.round(fg * a + bg * (1 - a));
  return (
    "#" +
    [mix(fill[0], sr), mix(fill[1], sg), mix(fill[2], sb)]
      .map((c) => c.toString(16).padStart(2, "0"))
      .join("")
  );
}

/** CSS `color-mix(in srgb, a, b <pct>%)` — `pct` is b's share, 0..1. */
export function mixSrgb(a: string, b: string, pct: number): string {
  const [ar, ag, ab] = parseFill(a);
  const [br, bg, bb] = parseFill(b);
  const at = (x: number, y: number) => Math.round(x * (1 - pct) + y * pct);
  return (
    "#" +
    [at(ar, br), at(ag, bg), at(ab, bb)].map((c) => c.toString(16).padStart(2, "0")).join("")
  );
}

/**
 * Resolve a CSS value that a component actually set — a token reference, a
 * bare hex, or an `rgba()` — into the solid colour it paints as over `surface`.
 */
export function paintedOver(css: string, block: RegExp, value: string, surface: string): string {
  const ref = /^var\(\s*(--[\w-]+)\s*\)$/.exec(value.trim());
  const raw = ref ? resolveValueIn(css, block, ref[1]) : value.trim();
  return over(parseFill(raw), surface);
}
