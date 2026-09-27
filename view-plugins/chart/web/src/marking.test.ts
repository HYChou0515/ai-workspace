/**
 * #847 PR 3 P2: a chart's rows ↔ a named marking. Reading lights each layer's
 * rows by the platform's one matching rule; writing turns a selection (or the
 * spec's `highlight:`) into picks on `keys:` (#861 D1).
 */
import { describe, expect, it } from "vitest";

import { type Marking, markingFrom, markingRows, MarkingStore } from "../../../../web/src/lib/markings";
import { highlightMarking, markedCount, markingLit, selectionMarking, stillWritten } from "./marking";
import { measuredFields } from "./option";
import { answer, cat, f64, layer, time } from "./testAnswer";

const A = answer(
  layer(
    "scatter",
    4,
    { x: f64([1, 2, 3, 4]), group: cat(["g1", "g1", "g2", "g3"]), item: f64([1, 2, 1, 7]) },
    { highlight: btoa(String.fromCharCode(0b0101)), lit: 2 },
  ),
  layer("bar", 2, { y: f64([5, 6]) }),
);

/** A marking as its keys and sorted rows, for comparing. */
const shape = (m: Marking | null) => m && { keys: [...m.keys], rows: markingRows(m) };

describe("markingLit", () => {
  it("lights each row of a layer that carries a marked key, by the platform's rule", () => {
    const lit = markingLit(A, markingFrom(["group"], [["g1"]]), []);
    expect(lit[0]).toEqual([true, true, false, false]);
  });

  it("with every key, lights the picks only -- not every combination of their values (#861 D1)", () => {
    // (g1, 2) and (g2, 1) picked: (g1, 1) holds a picked group AND a picked
    // item, but was not picked.
    const lit = markingLit(A, markingFrom(["group", "item"], [["g1", "2"], ["g2", "1"]]), []);
    expect(lit[0]).toEqual([false, true, true, false]);
  });

  it("a layer with some of the keys lights the rows that contain a pick (D2)", () => {
    const perGroup = answer(layer("bar", 3, { group: cat(["g1", "g2", "g3"]), v: f64([1, 2, 3]) }));
    const lit = markingLit(perGroup, markingFrom(["group", "item"], [["g1", "2"], ["g3", "9"]]), []);
    expect(lit[0]).toEqual([true, false, true]);
  });

  it("leaves a layer with none of the keys undimmed (null)", () => {
    expect(markingLit(A, markingFrom(["group"], [["g1"]]), [])[1]).toBeNull();
  });

  it("with no keys, still lights on a same-named column it carries", () => {
    // A view without `keys:` cannot write, but it can be lit (Q6): its layer
    // columns are what it shares with the marking.
    const lit = markingLit(A, markingFrom(["group"], [["g2"]]), []);
    expect(lit[0]).toEqual([false, false, true, false]);
  });
});

describe("markingLit reads keys the way selections write them (#855 keyColumn)", () => {
  const T = answer(
    layer("line", 3, {
      day: time(["2024-01-01", "2024-01-02", "2024-01-03"]),
      "$key.day": cat(["2024-01-01", "2024-01-02", "2024-01-03"]),
      group: cat(["g1", "g1", "g2"]),
      v: f64([1, 2, 3]),
    }),
  );

  it("a key a channel sends as time is lit by its marking strings", () => {
    expect(markingLit(T, markingFrom(["day"], [["2024-01-02"]]), [])[0]).toEqual([false, true, false]);
  });

  it("round trip: what a selection writes lights exactly the selected rows", () => {
    // selectionMarking is the oracle for what a marking holds; reading must agree.
    for (const keys of [["day"], ["day", "group"], ["group", "day"]]) {
      for (const rows of [[0], [1, 2], [0, 2]]) {
        const written = selectionMarking([{ source: "brush", layer: 0, rows }], T, keys, [])!;
        const lit = markingLit(T, written, [])[0]!;
        expect(lit.flatMap((on, r) => (on ? [r] : []))).toEqual(rows);
      }
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
      group: cat(["g1", "g2", "g1"]),
      item: f64([1, 2, 3]),
    }),
  );

  it("is lit on same-named text and number columns", () => {
    const written = selectionMarking([{ source: "brush", layer: 0, rows: [1] }], writer, ["group", "item"], [])!;
    const reader = answer(layer("bar", 3, { group: cat(["g2", "g1", "g2"]), item: f64([2, 2, 5]), v: f64([1, 2, 3]) }));
    expect(markingLit(reader, written, [])[0]).toEqual([true, false, false]);
  });

  it("a time column it only carries as a channel is not compared — drawn undimmed, never all-dim", () => {
    // Its column holds epoch ms, not the marking's strings: comparing them lit
    // nothing, which drew the whole chart as "no match".
    const written = selectionMarking([{ source: "brush", layer: 0, rows: [1] }], writer, ["day"], [])!;
    const reader = answer(layer("line", 3, { day: time(["2024-01-01", "2024-01-02", "2024-01-03"]), v: f64([1, 2, 3]) }));
    expect(markingLit(reader, written, [])[0]).toBeNull();
  });
});

describe("selectionMarking", () => {
  it("writes each selected row's keys as one pick, as the canon strings (#861 D1)", () => {
    // rows 0 and 2: (g1, 1) and (g2, 1) -- two picks, not {g1, g2} x {1}
    const m = selectionMarking([{ source: "brush", layer: 0, rows: [0, 2] }], A, ["item", "group"], []);
    expect(shape(m)).toEqual({ keys: ["group", "item"], rows: [["g1", "1"], ["g2", "1"]] });
  });

  it("the union of several layers' picks", () => {
    const two = answer(
      layer("scatter", 2, { group: cat(["g1", "g2"]), item: cat(["1", "2"]) }),
      layer("scatter", 2, { group: cat(["g3", "g1"]), item: cat(["3", "1"]) }),
    );
    const sel = [
      { source: "brush" as const, layer: 0, rows: [0] },
      { source: "brush" as const, layer: 1, rows: [0, 1] },
    ];
    expect(shape(selectionMarking(sel, two, ["group", "item"], []))).toEqual({
      keys: ["group", "item"],
      rows: [["g1", "1"], ["g3", "3"]],
    });
  });

  it("is null for a view without keys — it cannot write", () => {
    expect(selectionMarking([{ source: "brush", layer: 0, rows: [0] }], A, [], [])).toBeNull();
  });

  it("an empty selection is the empty marking (the write that clears)", () => {
    expect(shape(selectionMarking([], A, ["group"], []))).toEqual({ keys: ["group"], rows: [] });
  });
});

describe("highlightMarking", () => {
  it("is the spec highlight's lit rows as picks on keys", () => {
    expect(shape(highlightMarking(A, ["group"], []))).toEqual({ keys: ["group"], rows: [["g1"], ["g2"]] });
    expect(shape(highlightMarking(A, ["group", "item"], []))).toEqual({
      keys: ["group", "item"],
      rows: [["g1", "1"], ["g2", "1"]],
    });
  });

  it("is null when no layer carries a highlight", () => {
    expect(highlightMarking(answer(layer("bar", 2, { y: f64([5, 6]) })), ["group"], [])).toBeNull();
  });
});

describe("a binned layer carries no key (round 15 defect lens D2)", () => {
  // Above the bin threshold a scatter's rows are bins: `a` holds bin centres
  // (1.0078125 …), never a row's value, and the sandbox sends no `$key.a`.
  const B = answer(
    layer("scatter", 3, { a: f64([1.0078125, 2.0078125, 3.0078125]), $count: f64([4, 5, 6]) }, {
      binned: { points: 15, bins: 3 },
    }),
  );

  it("is drawn undimmed rather than lit by bin centres", () => {
    expect(markingLit(B, markingFrom(["a"], [["3"]]), [])).toEqual([null]);
  });

  it("writes nothing — not the empty marking that would clear every linked view", () => {
    expect(selectionMarking([{ source: "brush", layer: 0, rows: [2] }], B, ["a"], [])).toBeNull();
  });
});

describe("a selection that can name no key marks nothing (#847/#848 PR 5 P41 row 21)", () => {
  // Round 20's defect lens: a stack keyed by a row id (which `validate` now
  // refuses) carries no `id` column -- its rows are sums -- and a brush over
  // it wrote `{}`, the write that clears every linked view. As the table's
  // rule has it, a view that cannot name a row must never send an empty marking.
  const SUMS = answer(layer("bar", 2, { item: cat(["p", "q"]), value: f64([7, 13]) }));

  it("is null when no selected layer carries any key", () => {
    expect(selectionMarking([{ source: "brush", layer: 0, rows: [0, 1] }], SUMS, ["id"], [])).toBeNull();
  });

  it("is null when every key it carries is measured", () => {
    expect(selectionMarking([{ source: "brush", layer: 0, rows: [1] }], SUMS, ["value"], [new Set(["value"])])).toBeNull();
  });

  it("still writes what a layer that carries a key names", () => {
    const two = answer(layer("bar", 2, { item: cat(["p", "q"]), value: f64([7, 13]) }), layer("scatter", 1, { id: cat(["r1"]) }));
    const sel = [
      { source: "brush" as const, layer: 0, rows: [0] },
      { source: "brush" as const, layer: 1, rows: [0] },
    ];
    expect(shape(selectionMarking(sel, two, ["id"], []))).toEqual({ keys: ["id"], rows: [["r1"]] });
  });

  it("a layer naming fewer keys makes the picks coarser; one naming none of the others' keys shares none", () => {
    const two = answer(
      layer("bar", 2, { group: cat(["g1", "g2"]), value: f64([7, 13]) }),
      layer("scatter", 2, { group: cat(["g1", "g3"]), item: cat(["1", "2"]) }),
      layer("scatter", 1, { item: cat(["9"]) }),
    );
    const bar = { source: "brush" as const, layer: 0, rows: [1] };
    const pts = { source: "brush" as const, layer: 1, rows: [0, 1] };
    expect(shape(selectionMarking([bar, pts], two, ["group", "item"], []))).toEqual({
      keys: ["group"],
      rows: [["g1"], ["g2"], ["g3"]],
    });
    expect(selectionMarking([bar, { source: "brush", layer: 2, rows: [0] }], two, ["group", "item"], [])).toBeNull();
  });
});

describe("a key no selected row holds a value of (#847/#848 PR 5 P44 row 35)", () => {
  // Review round 22: a brush over rows whose key is empty kept the key with no
  // values, so the selection counted as naming it and wrote `{}` -- the write
  // that clears every linked view -- and the count beside "by item" counted
  // rows that gave the key nothing ("3 selected · by item" for one item).
  const E = answer(
    layer("scatter", 3, { x: f64([1, 2, 3]), item: cat([null, null, "r01"]), group: cat(["a", null, "b"]) }),
  );

  it("marks nothing from rows with no key value", () => {
    expect(selectionMarking([{ source: "brush", layer: 0, rows: [0, 1] }], E, ["item"], [])).toBeNull();
  });

  it("writes the picks the rows name", () => {
    expect(shape(selectionMarking([{ source: "brush", layer: 0, rows: [0, 1, 2] }], E, ["item"], []))).toEqual({
      keys: ["item"],
      rows: [["r01"]],
    });
  });

  it("drops a key it found no value of, keeping the others", () => {
    expect(shape(selectionMarking([{ source: "brush", layer: 0, rows: [0, 1] }], E, ["item", "group"], []))).toEqual({
      keys: ["group"],
      rows: [["a"]],
    });
  });

  it("a row missing one of the keys names no pick", () => {
    // row 0 (a, -) and row 2 (b, r01): only row 2 names both
    expect(shape(selectionMarking([{ source: "brush", layer: 0, rows: [0, 2] }], E, ["item", "group"], []))).toEqual({
      keys: ["group", "item"],
      rows: [["b", "r01"]],
    });
  });

  it("counts the selected rows that name a whole pick", () => {
    expect(markedCount([{ source: "brush", layer: 0, rows: [0, 1, 2] }], E, ["item"], [])).toBe(1);
    expect(markedCount([{ source: "brush", layer: 0, rows: [0, 1, 2] }], E, ["group"], [])).toBe(2);
    expect(markedCount([{ source: "brush", layer: 0, rows: [0, 1] }], E, ["item"], [])).toBe(0);
  });

  it("counts no row by a column a channel aggregates, as it writes none", () => {
    const sums = answer(layer("bar", 2, { item: cat(["p", "q"]), value: f64([7, 13]) }));
    expect(selectionMarking([{ source: "brush", layer: 0, rows: [0, 1] }], sums, ["value"], [new Set(["value"])])).toBeNull();
    expect(markedCount([{ source: "brush", layer: 0, rows: [0, 1] }], sums, ["value"], [new Set(["value"])])).toBe(0);
  });
});

describe("markedCount is the selected rows that name a whole pick (review #862 regression A1)", () => {
  // Four rows over two groups and two items, three distinct (group, item)
  // pairs. A chart says how many ROWS were selected, as on #855 and as its
  // linked table counts them (D1: a single key behaves as before) -- not the
  // three distinct picks the marking holds (the chip says those).
  const P = answer(layer("scatter", 4, { group: cat(["g1", "g1", "g2", "g1"]), item: cat(["1", "2", "1", "1"]) }));
  const all = [{ source: "brush" as const, layer: 0, rows: [0, 1, 2, 3] }];

  it("counts every selected row, not the distinct picks", () => {
    expect(markedCount(all, P, ["group", "item"], [])).toBe(4);
    expect(markingRows(selectionMarking(all, P, ["group", "item"], [])!)).toHaveLength(3);
    expect(markedCount(all, P, ["group"], [])).toBe(4);
  });

  it("is 0 for a view without keys", () => {
    expect(markedCount(all, P, [], [])).toBe(0);
  });

  it("counts each row once when two layers draw the same rows (a line with its points; review #862 round 2)", () => {
    // Both layers draw the 3 rows; a box over the chart selects them in each.
    // A linked table says 3 of 3 -- summing the layers said 6.
    const rows = { item: cat(["r1", "r2", "r3"]), v: f64([1, 2, 3]) };
    const both = answer(layer("line", 3, rows), layer("scatter", 3, rows));
    const box = [
      { source: "brush" as const, layer: 0, rows: [0, 1, 2] },
      { source: "brush" as const, layer: 1, rows: [0, 1, 2] },
    ];
    expect(markedCount(box, both, ["item"], [])).toBe(3);
  });

  it("counts every row when two layers draw different rows (each its own transform; review #862 round 3)", () => {
    // Layer 0 draws a1..a3, layer 1 b1..b3; one box over both selects all six.
    // The marking holds six picks and a linked table lights six rows.
    const both = answer(
      layer("scatter", 3, { item: cat(["a1", "a2", "a3"]), v: f64([1, 2, 3]) }),
      layer("scatter", 3, { item: cat(["b1", "b2", "b3"]), v: f64([1, 2, 3]) }),
    );
    const box = [
      { source: "brush" as const, layer: 0, rows: [0, 1, 2] },
      { source: "brush" as const, layer: 1, rows: [0, 1, 2] },
    ];
    expect(markedCount(box, both, ["item"], [])).toBe(6);
  });

  it("counts a row once when two layers select overlapping parts of the same rows (review #862 round 3)", () => {
    // The line's box takes r1, r2; the points' takes r2, r3: three rows went.
    const rows = { item: cat(["r1", "r2", "r3"]), v: f64([1, 2, 3]) };
    const both = answer(layer("line", 3, rows), layer("scatter", 3, rows));
    const box = [
      { source: "brush" as const, layer: 0, rows: [0, 1] },
      { source: "brush" as const, layer: 1, rows: [1, 2] },
    ];
    expect(markedCount(box, both, ["item"], [])).toBe(3);
  });
});

describe("an aggregated channel's column is not a key (#847/#848 PR 5 P32)", () => {
  // Found in P30's demo: a pie with `theta: {field: item, aggregate: count}` on
  // a marking keyed {group, item} lit the slices whose COUNT equalled a marked
  // item. The answer names the aggregate's column after its field (and repeats
  // it as `$key.item` when `item` is in `keys:`), but it holds counts, not items.
  const PIE = answer(
    layer(
      "pie",
      4,
      { group: cat(["A", "B", "C", "D"]), item: f64([6, 4, 3, 5]), "$key.item": cat([6, 4, 3, 5]) },
      { highlight: btoa(String.fromCharCode(0b0010)), lit: 1 },
    ),
  );
  const PIE_MEASURED = [new Set(["item"])];
  // rows (A, 4), (A, 5) and (B, 3) picked elsewhere: A and B contain a pick
  const PICKED = markingFrom(["group", "item"], [["A", "4"], ["A", "5"], ["B", "3"]]);

  it("a pie counting items lights whole by its group only (D4)", () => {
    expect(markingLit(PIE, PICKED, PIE_MEASURED)[0]).toEqual([true, true, false, false]);
  });

  it("a bar of an item mean lights by its group only", () => {
    const BAR = answer(layer("bar", 3, { group: cat(["A", "B", "C"]), item: f64([3.5, 2.5, 2]), "$key.item": cat([3.5, 2.5, 2]) }));
    // (A, 2) and (C, 2) picked: C's mean happens to be 2, A's is not
    const lit = markingLit(BAR, markingFrom(["group", "item"], [["A", "2"], ["C", "2"]]), [new Set(["item"])]);
    expect(lit[0]).toEqual([true, false, true]);
  });

  it("a layer whose only shared column is aggregated is drawn undimmed", () => {
    expect(markingLit(PIE, markingFrom(["item"], [["4"]]), PIE_MEASURED)).toEqual([null]);
  });

  it("a slice picked writes its group, not its count", () => {
    const m = selectionMarking([{ source: "click", layer: 0, rows: [0] }], PIE, ["group", "item"], PIE_MEASURED);
    expect(shape(m)).toEqual({ keys: ["group"], rows: [["A"]] });
  });

  it("a highlight seeds its group, not its count", () => {
    expect(shape(highlightMarking(PIE, ["group", "item"], PIE_MEASURED))).toEqual({ keys: ["group"], rows: [["B"]] });
  });

  it("(control) the same layer with nothing aggregated lights and writes by both columns", () => {
    // row (B, 4) is not a pick; (A, 6) is not either
    expect(markingLit(PIE, PICKED, [new Set()])[0]).toEqual([false, false, false, false]);
    const m = selectionMarking([{ source: "click", layer: 0, rows: [0] }], PIE, ["group", "item"], [new Set()]);
    expect(shape(m)).toEqual({ keys: ["group", "item"], rows: [["A", "6"]] });
  });
});

describe("measuredFields (P32, P40 row 18)", () => {
  // The answer says which fields each layer sends holding an aggregate --
  // or, in a stack the sandbox summed, the value its rows share -- so the
  // renderer never restates the sandbox's rule for it (a stack's value
  // channel is summed with no `aggregate` in the spec).
  it("reads, per layer, the fields the answer says it measured", () => {
    const a = answer(layer("bar", 1, {}, { measured: ["item"] }), layer("scatter", 1, {}));
    expect(measuredFields(a).map((s) => [...s])).toEqual([["item"], []]);
  });

  it("reads a stack's summed value and the fields it kept where shared", () => {
    const a = answer(layer("bar", 1, {}, { measured: ["region", "value"] }));
    expect(measuredFields(a).map((s) => [...s].sort())).toEqual([["region", "value"]]);
  });
});

describe("stillWritten (P34)", () => {
  // Whether the marking still holds exactly what a view wrote. The oracle is
  // the host's store: it takes a write as "the same again" -- and tells no
  // one -- exactly when the entry already holds it from the same source.
  const same = (held: Marking, heldFrom: string, wrote: Marking, from: string): boolean => {
    const store = new MarkingStore();
    store.set("m", held, heldFrom);
    let told = false;
    store.subscribe("m", () => (told = true));
    store.set("m", wrote, from);
    return !told;
  };
  const M = (keys: string[], ...rows: string[][]) => markingFrom(keys, rows);
  it.each<[Marking, string, Marking, string]>([
    [M(["group"], ["A"]), "/v/a", M(["group"], ["A"]), "/v/a"],
    [M(["group"], ["A"]), "/v/b", M(["group"], ["A"]), "/v/a"],
    [M(["group"], ["A"], ["B"]), "/v/a", M(["group"], ["B"], ["A"]), "/v/a"],
    [M(["group"], ["A"], ["B"]), "/v/a", M(["group"], ["A"]), "/v/a"],
    [M(["group"], ["A"]), "/v/a", M(["group"], ["A"], ["B"]), "/v/a"],
    [M(["group"], ["A"]), "/v/a", M(["item"], ["A"]), "/v/a"],
    [M(["group", "item"], ["A", "1"]), "/v/a", M(["item", "group"], ["1", "A"]), "/v/a"],
    [M(["group", "item"], ["A", "1"]), "/v/a", M(["group"], ["A"]), "/v/a"],
    [M(["group"], ["A"]), "/v/a", M(["group", "item"], ["A", "1"]), "/v/a"],
    // same keys and the same values per column, different picks
    [M(["group", "item"], ["A", "1"], ["B", "2"]), "/v/a", M(["group", "item"], ["A", "2"], ["B", "1"]), "/v/a"],
  ])("agrees with the store: held %o from %s, wrote %o from %s", (held, heldFrom, wrote, from) => {
    const store = new MarkingStore();
    store.set("m", held, heldFrom);
    expect(stillWritten(store.get("m"), wrote, from)).toBe(same(held, heldFrom, wrote, from));
  });

  it("an empty write is still what the marking holds while it holds nothing", () => {
    expect(stillWritten(undefined, M(["group"]), "/v/a")).toBe(true);
    expect(stillWritten({ marking: M(["group"], ["A"]), source: "/v/a" }, M(["group"]), "/v/a")).toBe(false);
    expect(stillWritten(undefined, M(["group"], ["A"]), "/v/a")).toBe(false);
  });
});
