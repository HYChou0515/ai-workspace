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
