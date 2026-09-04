import { describe, expect, it } from "vitest";

import type { EntityFieldSpec } from "../../api/entities";
import { actorPalette, hueFraction } from "./actorColor";

/**
 * `color_by` on an ACTOR field colours a gantt bar by who owns the work. The
 * status palette cannot serve it: that one hashes into six fixed slots, which
 * is a closed vocabulary's shape, not a directory's. Four people already
 * collide 44% of the time and seven collide with certainty — at which point the
 * colour has stopped answering "whose bar is this".
 */
describe("actorPalette", () => {
  it("gives 20 people 20 distinct colours", () => {
    const ids = Array.from({ length: 20 }, (_, i) => `u${i}`);
    const colour = actorPalette(ids);
    const distinct = new Set(ids.map((id) => colour(id).bg));
    expect(distinct.size).toBe(20);
  });

  /**
   * The promise a growing team needs: hues crowd, but they never land on top of
   * each other, and there is no size at which the palette gives up. Asserting
   * the BOUND rather than a pinned list is what makes a different sequence with
   * a worse spread fail here — a sequence that merely produced 64 distinct
   * numbers would pass the test above and still put two people 0.1° apart.
   */
  it("never puts two people closer than the sequence's bound, at any team size", () => {
    for (let n = 2; n <= 64; n++) {
      const hues = Array.from({ length: n }, (_, k) => hueFraction(k) * 360).sort((a, b) => a - b);
      const gaps = hues.map((h, i) => (i === n - 1 ? 360 + hues[0] - h : hues[i + 1] - h));
      const bound = 360 / 2 ** Math.ceil(Math.log2(n));
      expect(Math.min(...gaps), `n=${n}`).toBeCloseTo(bound, 6);
    }
  });

  /**
   * Work nobody owns is not a person, and spending a seat on it would push the
   * people who ARE on the chart closer together for nothing.
   */
  /**
   * The head count is deliberately uncapped, so the palette has to keep its
   * promise past any size a team plausibly reaches. This is also what catches a
   * chroma raised beyond what sRGB holds at this lightness: colours that have
   * to give up chroma to fit slide toward the gamut boundary, and if they slide
   * far enough they land on top of each other — distinctness fails here long
   * before anyone notices the bars look flat.
   */
  it("still gives 256 people 256 distinct colours", () => {
    // 256 is far past a legible chart and still inside the sequence's useful
    // range; the docstring's stated ceiling (~300, where 8-bit sRGB stops being
    // able to tell two hue steps apart) is what this is walking toward, and a
    // chroma raised past the gamut would fail here long before reaching it.
    const ids = Array.from({ length: 256 }, (_, i) => `u${i}`);
    const colour = actorPalette(ids);
    expect(new Set(ids.map((id) => colour(id).bg)).size).toBe(256);
  });

  /**
   * `colors:` is the schema's pin, and `selectColor` has always honoured it. An
   * actor field taking a different palette must not make that key quietly stop
   * working — a setting that silently does nothing is worse than one that is
   * refused.
   */
  const pinned = (colors: Record<string, string>): EntityFieldSpec => ({
    name: "assignee",
    role: "actor",
    colors,
  });

  it("honours a schema pin, in the named hue's own family", () => {
    const colour = actorPalette(["ana", "bo"], pinned({ ana: "teal" }));
    // teal is the cool green-blue the teal chip is: g strongest, r weakest.
    const [, r, g, b] = /^#(\w\w)(\w\w)(\w\w)$/.exec(colour("ana").bg) ?? [];
    const [R, G, B] = [r, g, b].map((h) => parseInt(h, 16));
    expect(G).toBeGreaterThan(R);
    expect(B).toBeGreaterThan(R);
    expect(colour("ana").bg).not.toBe(colour("bo").bg);
  });

  it("spends no seat on a pinned person", () => {
    // `bo` is the only unpinned name either way, so it must hold seat 0.
    const withPin = actorPalette(["ana", "bo"], pinned({ ana: "teal" }))("bo");
    const alone = actorPalette(["bo"])("bo");
    expect(withPin).toEqual(alone);
  });

  it("gives a pin that names no hue the unowned treatment", () => {
    const colour = actorPalette(["ana"], pinned({ ana: "chartreuse" }));
    expect(colour("ana").bg).toBe(actorPalette([])("").bg);
  });

  it("draws unowned work with no hue at all", () => {
    const [, r, g, b] = /^#(\w\w)(\w\w)(\w\w)$/.exec(actorPalette(["ana"])("").bg) ?? [];
    expect([r, g, b]).toEqual([r, r, r]);
  });

  it("spends no seat on unowned work", () => {
    // `bo` must land on the same colour either way — which it only can if the
    // blank rows between them consumed nothing.
    const withBlanks = actorPalette(["ana", "", "ana", "", "bo"])("bo");
    const without = actorPalette(["ana", "bo"])("bo");
    expect(withBlanks).toEqual(without);
  });

  it("gives an id that is on no record the same unowned treatment", () => {
    const colour = actorPalette(["ana"]);
    expect(colour("someone-who-left").bg).toBe(colour("").bg);
  });
});
