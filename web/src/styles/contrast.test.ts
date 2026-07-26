import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * a11y guard (#456): the dimmest text tier (`--text-paper-d2`, used for
 * metadata / hints / placeholders) must stay legible on the surface it sits on.
 * WCAG 2.1 AA asks for 4.5:1 on normal text; these labels render small, so we
 * hold the line at a 4:1 floor — well above the ~2.9:1 the original #8A8C90
 * gave on cream, which read as barely-there grey. Guarding the RATIO (not a
 * pinned hex) is what forces the token to stay dark enough if it's ever re-tuned.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const TOKENS_PATH = resolve(HERE, "tokens.css");

/** Pull a hex token's value out of a specific `:root` / `[data-theme]` block. */
function tokenIn(css: string, block: RegExp, name: string): string {
  const m = block.exec(css);
  if (!m) throw new Error(`block ${block} not found`);
  const hit = new RegExp(`${name}:\\s*(#[0-9A-Fa-f]{6})`).exec(m[0]);
  if (!hit) throw new Error(`${name} not found in block`);
  return hit[1];
}

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

function contrast(a: string, b: string): number {
  const la = relLuminance(a);
  const lb = relLuminance(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

// The light `:root` block and the dark `[data-theme="dark"]` override block.
const LIGHT = /:root\s*\{[\s\S]*?\n\s*\}/;
const DARK = /\[data-theme="dark"\]\s*\{[\s\S]*?\n\s*\}/;

/** The raw value of a custom property in a block (rgba(...) / hex / var(...)). */
function rawValueIn(css: string, block: RegExp, name: string): string {
  const m = block.exec(css);
  if (!m) throw new Error(`block ${block} not found`);
  const hit = new RegExp(`${name}:\\s*([^;]+);`).exec(m[0]);
  if (!hit) throw new Error(`${name} not found in block`);
  return hit[1].trim();
}

/** Parse an `rgba(r,g,b,a)` (or `rgb(...)` / `#rrggbb`) fill → [r,g,b,a]. */
function parseFill(v: string): [number, number, number, number] {
  const rgba = /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)/.exec(v);
  if (rgba) return [Number(rgba[1]), Number(rgba[2]), Number(rgba[3]), rgba[4] ? Number(rgba[4]) : 1];
  const hex = /#([0-9A-Fa-f]{6})/.exec(v);
  if (!hex) throw new Error(`unparseable fill: ${v}`);
  const h = hex[1];
  return [Number.parseInt(h.slice(0, 2), 16), Number.parseInt(h.slice(2, 4), 16), Number.parseInt(h.slice(4, 6), 16), 1];
}

/** Alpha-composite a fill over an opaque surface hex → a solid `#rrggbb`. */
function over(fill: [number, number, number, number], surface: string): string {
  const [sr, sg, sb] = [
    Number.parseInt(surface.slice(1, 3), 16),
    Number.parseInt(surface.slice(3, 5), 16),
    Number.parseInt(surface.slice(5, 7), 16),
  ];
  const a = fill[3];
  const mix = (fg: number, bg: number) => Math.round(fg * a + bg * (1 - a));
  return "#" + [mix(fill[0], sr), mix(fill[1], sg), mix(fill[2], sb)].map((c) => c.toString(16).padStart(2, "0")).join("");
}

describe("text-paper-d2 contrast (#456)", () => {
  it("clears a 4:1 floor against the cream surface in light mode", () => {
    const css = readFileSync(TOKENS_PATH, "utf8");
    const d2 = tokenIn(css, LIGHT, "--text-paper-d2");
    const paper = tokenIn(css, LIGHT, "--paper");
    expect(contrast(d2, paper)).toBeGreaterThanOrEqual(4);
  });

  it("clears a 4:1 floor against the ink surface in dark mode", () => {
    const css = readFileSync(TOKENS_PATH, "utf8");
    const d2 = tokenIn(css, DARK, "--text-paper-d2");
    const paper = tokenIn(css, DARK, "--paper");
    expect(contrast(d2, paper)).toBeGreaterThanOrEqual(4);
  });
});

describe("categorical chip contrast (#GH-projects B / #4)", () => {
  // A status/label chip is a coloured `--cat-N-fg` ink on a translucent
  // `--cat-N-bg` fill over the theme paper. The ink must stay legible in BOTH
  // themes — the dark block MUST override these (a mid-tone ink tuned for cream
  // reads at ~2.9:1 on ink, below the bar). Chips are bold, short labels, so we
  // hold a 3:1 UI-component floor (WCAG 1.4.11) rather than the 4.5:1 body bar.
  const SLOTS = [1, 2, 3, 4, 5, 6]; // 7 = neutral (surface token, not a hue)

  for (const [label, block] of [
    ["light mode (on cream)", LIGHT],
    ["dark mode (on ink)", DARK],
  ] as const) {
    it(`keeps every coloured chip ≥3:1 in ${label}`, () => {
      const css = readFileSync(TOKENS_PATH, "utf8");
      const paper = tokenIn(css, block, "--paper");
      for (const n of SLOTS) {
        const fg = tokenIn(css, block, `--cat-${n}-fg`);
        const fill = parseFill(rawValueIn(css, block, `--cat-${n}-bg`));
        const ratio = contrast(fg, over(fill, paper));
        expect(ratio, `--cat-${n} in ${label} = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(3);
      }
    });
  }
});
