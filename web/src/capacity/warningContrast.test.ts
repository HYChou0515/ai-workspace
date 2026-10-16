import { describe, expect, it } from "vitest";

import { DARK, LIGHT, TOKENS_CSS, contrast, mixSrgb, over, parseFill, tokenIn } from "../test/contrast";
import { effective } from "../test/cssRules";
import { readSrcFile } from "../test/readSrcFile";

/**
 * The capacity page's warning box — a failed load, the what-if's "僅供參考"
 * and "capped at N" notes — is full sentences in `--text-body-sm`, so it is
 * held to the 4:1 small-text floor `styles/contrast.test.ts` holds the dimmest
 * text tier to. `--err` as the text colour sat at 3.58:1 on its own tint in
 * light mode and 3.57:1 in dark; the box now copies `.ev-banner--err`
 * (entity-views.css): body ink, with the error tone carried by the stripe and
 * the fill.
 *
 * Read from the stylesheet, not the DOM: the test environment does not cascade
 * (see `test/contrast.ts`).
 */

const CAPACITY_CSS = readSrcFile("styles/capacity.css");

/** The page surface the box sits on: `.capacity-page`'s own background. */
function surface(block: RegExp): string {
  const value = effective(CAPACITY_CSS, ".capacity-page", "background") ?? "";
  return painted(value, block, "#000000");
}

/**
 * A colour the rule set, as the solid colour it paints over `under`: a token,
 * an `rgba()` / hex, or `color-mix(in srgb, <token> P%, <token> | transparent)`.
 */
function painted(value: string, block: RegExp, under: string): string {
  const token = (ref: string) => {
    const name = /^var\(\s*(--[\w-]+)\s*\)$/.exec(ref.trim());
    if (!name) throw new Error(`not a token reference: ${ref}`);
    return tokenIn(TOKENS_CSS, block, name[1]);
  };
  const mix = /^color-mix\(in srgb,\s*(var\([^)]+\))\s+([\d.]+)%,\s*(transparent|var\([^)]+\))\s*\)$/.exec(
    value.trim(),
  );
  if (mix) {
    const share = Number(mix[2]) / 100;
    const [r, g, b] = parseFill(token(mix[1]));
    if (mix[3] === "transparent") return over([r, g, b, share], under);
    return mixSrgb(token(mix[1]), token(mix[3]), 1 - share);
  }
  if (value.trim().startsWith("var(")) return token(value);
  return over(parseFill(value), under);
}

describe("capacity warning contrast", () => {
  for (const [label, block] of [
    ["light mode (on cream)", LIGHT],
    ["dark mode (on ink)", DARK],
  ] as const) {
    it(`keeps the warning text at 4:1 or more in ${label}`, () => {
      const page = surface(block);
      const fill = painted(effective(CAPACITY_CSS, ".capacity-warning", "background") ?? "", block, page);
      const ink = painted(effective(CAPACITY_CSS, ".capacity-warning", "color") ?? "", block, fill);
      const ratio = contrast(ink, fill);
      expect(ratio, `.capacity-warning in ${label} = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4);
    });
  }
});
