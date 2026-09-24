/**
 * #847 PR 3 P2: a chart's rows ↔ a named marking. Reading lights each layer's
 * rows by the platform's one matching rule; writing projects a selection (or
 * the spec's `highlight:`) onto `keys:`.
 */
import { describe, expect, it } from "vitest";

import { isLit } from "../../../../web/src/lib/markings";
import { highlightMarking, markingLit, selectionMarking } from "./marking";
import { answer, cat, f64, layer } from "./testAnswer";

const A = answer(
  layer(
    "scatter",
    4,
    { x: f64([1, 2, 3, 4]), lot: cat(["L1", "L1", "L2", "L3"]), wafer: f64([1, 2, 1, 7]) },
    { highlight: btoa(String.fromCharCode(0b0101)), lit: 2 },
  ),
  layer("bar", 2, { y: f64([5, 6]) }),
);

describe("markingLit", () => {
  it("lights each row of a layer that carries a marked column, by the platform's rule", () => {
    const lit = markingLit(A, { lot: new Set(["L1"]) }, isLit);
    expect(lit[0]).toEqual([true, true, false, false]);
  });

  it("uses every key the layer carries", () => {
    const lit = markingLit(A, { lot: new Set(["L1"]), wafer: new Set(["2"]) }, isLit);
    expect(lit[0]).toEqual([false, true, false, false]);
  });

  it("leaves a layer with none of the keys undimmed (null)", () => {
    expect(markingLit(A, { lot: new Set(["L1"]) }, isLit)[1]).toBeNull();
  });

  it("with no keys, still lights on a same-named column it carries", () => {
    // A view without `keys:` cannot write, but it can be lit (Q6): its layer
    // columns are what it shares with the marking.
    const lit = markingLit(A, { lot: new Set(["L2"]) }, isLit);
    expect(lit[0]).toEqual([false, false, true, false]);
  });
});

describe("selectionMarking", () => {
  it("projects the selected rows onto keys, as the canon strings", () => {
    const m = selectionMarking([{ source: "brush", layer: 0, rows: [0, 2] }], A, ["lot", "wafer"]);
    expect(m && Object.fromEntries(Object.entries(m).map(([c, v]) => [c, [...v].sort()]))).toEqual({
      lot: ["L1", "L2"],
      wafer: ["1"],
    });
  });

  it("is null for a view without keys — it cannot write", () => {
    expect(selectionMarking([{ source: "brush", layer: 0, rows: [0] }], A, [])).toBeNull();
  });

  it("an empty selection is the empty marking (the write that clears)", () => {
    expect(selectionMarking([], A, ["lot"])).toEqual({});
  });
});

describe("highlightMarking", () => {
  it("is the spec highlight's lit rows projected onto keys", () => {
    const m = highlightMarking(A, ["lot"]);
    expect(m && [...m.lot!].sort()).toEqual(["L1", "L2"]);
  });

  it("is null when no layer carries a highlight", () => {
    expect(highlightMarking(answer(layer("bar", 2, { y: f64([5, 6]) })), ["lot"])).toBeNull();
  });
});
