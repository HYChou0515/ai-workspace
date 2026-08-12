import { describe, expect, it } from "vitest";

import { DARK, LIGHT, TOKENS_CSS, contrast, over, parseFill, rawValueIn, tokenIn } from "../test/contrast";

/**
 * a11y guard (#456): the dimmest text tier (`--text-paper-d2`, used for
 * metadata / hints / placeholders) must stay legible on the surface it sits on.
 * WCAG 2.1 AA asks for 4.5:1 on normal text; these labels render small, so we
 * hold the line at a 4:1 floor — well above the ~2.9:1 the original #8A8C90
 * gave on cream, which read as barely-there grey. Guarding the RATIO (not a
 * pinned hex) is what forces the token to stay dark enough if it's ever re-tuned.
 *
 * The maths lives in `src/test/contrast.ts` — the gantt bar guard
 * (`renderers/entity/ganttBarContrast.test.ts`) shares it.
 */

describe("text-paper-d2 contrast (#456)", () => {
  it("clears a 4:1 floor against the cream surface in light mode", () => {
    const d2 = tokenIn(TOKENS_CSS, LIGHT, "--text-paper-d2");
    const paper = tokenIn(TOKENS_CSS, LIGHT, "--paper");
    expect(contrast(d2, paper)).toBeGreaterThanOrEqual(4);
  });

  it("clears a 4:1 floor against the ink surface in dark mode", () => {
    const d2 = tokenIn(TOKENS_CSS, DARK, "--text-paper-d2");
    const paper = tokenIn(TOKENS_CSS, DARK, "--paper");
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
      const paper = tokenIn(TOKENS_CSS, block, "--paper");
      for (const n of SLOTS) {
        const fg = tokenIn(TOKENS_CSS, block, `--cat-${n}-fg`);
        const fill = parseFill(rawValueIn(TOKENS_CSS, block, `--cat-${n}-bg`));
        const ratio = contrast(fg, over(fill, paper));
        expect(ratio, `--cat-${n} in ${label} = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(3);
      }
    });
  }
});

/**
 * The board card's record number (#PM). It renders on the CARD surface, not the
 * page surface, and in `--text-paper-d` — a tier brighter than the d2 guarded
 * above, so the test above does not cover it. Guarded in BOTH themes on purpose:
 * #690 shipped text that was unreadable in light mode because the colour had only
 * ever been eyeballed in dark, and the unit tests stayed green throughout.
 */
describe("board card number contrast", () => {
  it("clears a 4:1 floor on the card surface in light mode", () => {
    const ink = tokenIn(TOKENS_CSS, LIGHT, "--text-paper-d");
    const card = tokenIn(TOKENS_CSS, LIGHT, "--white");
    expect(contrast(ink, card)).toBeGreaterThanOrEqual(4);
  });

  it("clears a 4:1 floor on the card surface in dark mode", () => {
    const ink = tokenIn(TOKENS_CSS, DARK, "--text-paper-d");
    const card = tokenIn(TOKENS_CSS, DARK, "--white");
    expect(contrast(ink, card)).toBeGreaterThanOrEqual(4);
  });
});
