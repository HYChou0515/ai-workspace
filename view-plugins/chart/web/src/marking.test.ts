/**
 * #847 PR 3 P2: a chart's rows ↔ a named marking. Reading lights each layer's
 * rows by the platform's one matching rule; writing projects a selection (or
 * the spec's `highlight:`) onto `keys:`.
 */
import { describe, expect, it } from "vitest";

import { isLit } from "../../../../web/src/lib/markings";
import { highlightMarking, markingLit, selectionMarking } from "./marking";
import { answer, cat, f64, layer, time } from "./testAnswer";

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

describe("markingLit reads keys the way selections write them (#855 keyColumn)", () => {
  const T = answer(
    layer("line", 3, {
      day: time(["2024-01-01", "2024-01-02", "2024-01-03"]),
      "$key.day": cat(["2024-01-01", "2024-01-02", "2024-01-03"]),
      v: f64([1, 2, 3]),
    }),
  );

  it("a key a channel sends as time is lit by its marking strings", () => {
    expect(markingLit(T, { day: new Set(["2024-01-02"]) }, isLit)[0]).toEqual([false, true, false]);
  });

  it("round trip: what a selection writes lights exactly the selected rows", () => {
    // selectionValues is the oracle for what a marking holds; reading must agree.
    for (const rows of [[0], [1, 2], [0, 2]]) {
      const written = selectionMarking([{ source: "brush", layer: 0, rows }], T, ["day"])!;
      const lit = markingLit(T, written, isLit)[0]!;
      expect(lit.flatMap((on, r) => (on ? [r] : []))).toEqual(rows);
    }
  });
});

describe("a view without keys: reading what another view wrote (Q6)", () => {
  // The writer names `day` in keys:, so the sandbox sends `$key.day`; a reader
  // without keys: gets only its channel's own column.
  const writer = answer(
    layer("line", 3, {
      day: time(["2024-01-01", "2024-01-02", "2024-01-03"]),
      "$key.day": cat(["2024-01-01", "2024-01-02", "2024-01-03"]),
      lot: cat(["L1", "L2", "L1"]),
      wafer: f64([1, 2, 3]),
    }),
  );
  const written = selectionMarking([{ source: "brush", layer: 0, rows: [1] }], writer, ["day", "lot", "wafer"])!;

  it("is lit on a same-named text or number column", () => {
    const reader = answer(layer("bar", 3, { lot: cat(["L2", "L1", "L2"]), wafer: f64([2, 2, 5]), v: f64([1, 2, 3]) }));
    const onlyLotWafer = { lot: written.lot!, wafer: written.wafer! };
    expect(markingLit(reader, onlyLotWafer, isLit)[0]).toEqual([true, false, false]);
  });

  it("a time column it only carries as a channel is not compared — drawn undimmed, never all-dim", () => {
    // Its column holds epoch ms, not the marking's strings: comparing them lit
    // nothing, which drew the whole chart as "no match".
    const reader = answer(layer("line", 3, { day: time(["2024-01-01", "2024-01-02", "2024-01-03"]), v: f64([1, 2, 3]) }));
    expect(markingLit(reader, { day: written.day! }, isLit)[0]).toBeNull();
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
