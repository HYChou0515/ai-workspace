/**
 * #847 PR 3 P2: a chart's rows ↔ a named marking. Reading lights each layer's
 * rows by the platform's one matching rule; writing projects a selection (or
 * the spec's `highlight:`) onto `keys:`.
 */
import { describe, expect, it } from "vitest";

import { isLit, markedBy as hostMarkedBy, MarkingStore } from "../../../../web/src/lib/markings";
import { highlightMarking, markedBy, markingLit, type MarkingValues, selectionMarking, stillWritten } from "./marking";
import { measuredFields } from "./option";
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
    const lit = markingLit(A, { lot: new Set(["L1"]) }, isLit, []);
    expect(lit[0]).toEqual([true, true, false, false]);
  });

  it("uses every key the layer carries", () => {
    const lit = markingLit(A, { lot: new Set(["L1"]), wafer: new Set(["2"]) }, isLit, []);
    expect(lit[0]).toEqual([false, true, false, false]);
  });

  it("leaves a layer with none of the keys undimmed (null)", () => {
    expect(markingLit(A, { lot: new Set(["L1"]) }, isLit, [])[1]).toBeNull();
  });

  it("with no keys, still lights on a same-named column it carries", () => {
    // A view without `keys:` cannot write, but it can be lit (Q6): its layer
    // columns are what it shares with the marking.
    const lit = markingLit(A, { lot: new Set(["L2"]) }, isLit, []);
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
    expect(markingLit(T, { day: new Set(["2024-01-02"]) }, isLit, [])[0]).toEqual([false, true, false]);
  });

  it("round trip: what a selection writes lights exactly the selected rows", () => {
    // selectionValues is the oracle for what a marking holds; reading must agree.
    for (const rows of [[0], [1, 2], [0, 2]]) {
      const written = selectionMarking([{ source: "brush", layer: 0, rows }], T, ["day"], [])!;
      const lit = markingLit(T, written, isLit, [])[0]!;
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
  const written = selectionMarking([{ source: "brush", layer: 0, rows: [1] }], writer, ["day", "lot", "wafer"], [])!;

  it("is lit on a same-named text or number column", () => {
    const reader = answer(layer("bar", 3, { lot: cat(["L2", "L1", "L2"]), wafer: f64([2, 2, 5]), v: f64([1, 2, 3]) }));
    const onlyLotWafer = { lot: written.lot!, wafer: written.wafer! };
    expect(markingLit(reader, onlyLotWafer, isLit, [])[0]).toEqual([true, false, false]);
  });

  it("a time column it only carries as a channel is not compared — drawn undimmed, never all-dim", () => {
    // Its column holds epoch ms, not the marking's strings: comparing them lit
    // nothing, which drew the whole chart as "no match".
    const reader = answer(layer("line", 3, { day: time(["2024-01-01", "2024-01-02", "2024-01-03"]), v: f64([1, 2, 3]) }));
    expect(markingLit(reader, { day: written.day! }, isLit, [])[0]).toBeNull();
  });
});

describe("selectionMarking", () => {
  it("projects the selected rows onto keys, as the canon strings", () => {
    const m = selectionMarking([{ source: "brush", layer: 0, rows: [0, 2] }], A, ["lot", "wafer"], []);
    expect(m && Object.fromEntries(Object.entries(m).map(([c, v]) => [c, [...v].sort()]))).toEqual({
      lot: ["L1", "L2"],
      wafer: ["1"],
    });
  });

  it("is null for a view without keys — it cannot write", () => {
    expect(selectionMarking([{ source: "brush", layer: 0, rows: [0] }], A, [], [])).toBeNull();
  });

  it("an empty selection is the empty marking (the write that clears)", () => {
    expect(selectionMarking([], A, ["lot"], [])).toEqual({});
  });
});

describe("highlightMarking", () => {
  it("is the spec highlight's lit rows projected onto keys", () => {
    const m = highlightMarking(A, ["lot"], []);
    expect(m && [...m.lot!].sort()).toEqual(["L1", "L2"]);
  });

  it("is null when no layer carries a highlight", () => {
    expect(highlightMarking(answer(layer("bar", 2, { y: f64([5, 6]) })), ["lot"], [])).toBeNull();
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
    expect(markingLit(B, { a: new Set(["3"]) }, isLit, [])).toEqual([null]);
  });

  it("writes nothing — not the `{}` that would clear every linked view", () => {
    expect(selectionMarking([{ source: "brush", layer: 0, rows: [2] }], B, ["a"], [])).toBeNull();
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
  // rows (A, 4), (A, 5) and (B, 3) picked elsewhere: A and B are marked
  const PICKED = { group: new Set(["A", "B"]), item: new Set(["3", "4", "5"]) };

  it("a pie counting items lights by its group only", () => {
    expect(markingLit(PIE, PICKED, isLit, PIE_MEASURED)[0]).toEqual([true, true, false, false]);
  });

  it("a bar of an item mean lights by its group only", () => {
    const BAR = answer(layer("bar", 3, { group: cat(["A", "B", "C"]), item: f64([3.5, 2.5, 2]), "$key.item": cat([3.5, 2.5, 2]) }));
    // (A, 2) and (C, 2) picked: C's mean happens to be 2, A's is not
    const lit = markingLit(BAR, { group: new Set(["A", "C"]), item: new Set(["2"]) }, isLit, [new Set(["item"])]);
    expect(lit[0]).toEqual([true, false, true]);
  });

  it("a layer whose only shared column is aggregated is drawn undimmed", () => {
    expect(markingLit(PIE, { item: new Set(["4"]) }, isLit, PIE_MEASURED)).toEqual([null]);
  });

  it("a slice picked writes its group, not its count", () => {
    const m = selectionMarking([{ source: "click", layer: 0, rows: [0] }], PIE, ["group", "item"], PIE_MEASURED);
    expect(m && Object.fromEntries(Object.entries(m).map(([c, v]) => [c, [...v]]))).toEqual({ group: ["A"] });
  });

  it("a highlight seeds its group, not its count", () => {
    const m = highlightMarking(PIE, ["group", "item"], PIE_MEASURED);
    expect(m && Object.fromEntries(Object.entries(m).map(([c, v]) => [c, [...v]]))).toEqual({ group: ["B"] });
  });

  it("(control) the same layer with nothing aggregated lights and writes by both columns", () => {
    expect(markingLit(PIE, PICKED, isLit, [new Set()])[0]).toEqual([false, true, false, false]);
    const m = selectionMarking([{ source: "click", layer: 0, rows: [0] }], PIE, ["group", "item"], [new Set()]);
    expect(m && Object.keys(m)).toEqual(["group", "item"]);
  });
});

describe("measuredFields (P32)", () => {
  it("names, per layer, the fields a channel aggregates", () => {
    const doc = {
      view: "chart",
      source: "a.csv",
      layer: [
        { mark: "bar", encoding: { x: { field: "group", type: "nominal" }, y: { field: "item", type: "quantitative", aggregate: "mean" } } },
        { mark: "scatter", encoding: { x: { field: "group", type: "nominal" }, y: { field: "item", type: "quantitative" } } },
      ],
    };
    expect(measuredFields(doc).map((s) => [...s])).toEqual([["item"], []]);
  });

  it("reads a single-layer spec, and a tooltip list", () => {
    const doc = {
      view: "chart",
      source: "a.csv",
      mark: "pie",
      encoding: {
        theta: { field: "item", type: "quantitative", aggregate: "count" },
        color: { field: "group", type: "nominal" },
        tooltip: [{ field: "value", type: "quantitative", aggregate: "sum" }],
      },
    };
    expect(measuredFields(doc).map((s) => [...s].sort())).toEqual([["item", "value"]]);
  });
});

describe("markedBy (P27)", () => {
  // The host's tables say the same words beside their counts: the host's
  // `markedBy` is the oracle, so a chart and a table on one marking agree.
  it.each([
    [{ lot: new Set(["L1"]) }],
    [{ lot: new Set(["L1"]), wafer: new Set(["1", "2"]) }],
    [{ wafer: new Set(["1"]), lot: new Set(["L1"]), "a column": new Set(["x"]) }],
  ])("says what the host says for %o", (m) => {
    expect(markedBy(m)).toBe(hostMarkedBy(m));
  });

  it("names the columns in the order the marking was written", () => {
    expect(markedBy({ wafer: new Set(["1"]), lot: new Set(["L1"]) })).toBe("by wafer, lot");
  });
});

describe("stillWritten (P34)", () => {
  // Whether the marking still holds exactly what a view wrote. The oracle is
  // the host's store: it takes a write as "the same again" -- and tells no
  // one -- exactly when the entry already holds it from the same source.
  const same = (held: MarkingValues, heldFrom: string, wrote: MarkingValues, from: string): boolean => {
    const store = new MarkingStore();
    store.set("m", held, heldFrom);
    let told = false;
    store.subscribe("m", () => (told = true));
    store.set("m", wrote, from);
    return !told;
  };
  const S = (...v: string[]) => new Set(v);
  it.each<[MarkingValues, string, MarkingValues, string]>([
    [{ group: S("A") }, "/v/a", { group: S("A") }, "/v/a"],
    [{ group: S("A") }, "/v/b", { group: S("A") }, "/v/a"],
    [{ group: S("A", "B") }, "/v/a", { group: S("B", "A") }, "/v/a"],
    [{ group: S("A", "B") }, "/v/a", { group: S("A") }, "/v/a"],
    [{ group: S("A") }, "/v/a", { group: S("A", "B") }, "/v/a"],
    [{ group: S("A") }, "/v/a", { item: S("A") }, "/v/a"],
    [{ group: S("A"), item: S("1") }, "/v/a", { item: S("1"), group: S("A") }, "/v/a"],
    [{ group: S("A"), item: S("1") }, "/v/a", { group: S("A") }, "/v/a"],
    [{ group: S("A") }, "/v/a", { group: S("A"), item: S("1") }, "/v/a"],
    [{ group: S("A") }, "/v/a", { group: S("A"), item: S() }, "/v/a"],
  ])("agrees with the store: held %o from %s, wrote %o from %s", (held, heldFrom, wrote, from) => {
    const store = new MarkingStore();
    store.set("m", held, heldFrom);
    expect(stillWritten(store.get("m"), wrote, from)).toBe(same(held, heldFrom, wrote, from));
  });

  it("an empty write is still what the marking holds while it holds nothing", () => {
    expect(stillWritten(undefined, {}, "/v/a")).toBe(true);
    expect(stillWritten(undefined, { group: S() }, "/v/a")).toBe(true);
    expect(stillWritten({ marking: { group: S("A") }, source: "/v/a" }, {}, "/v/a")).toBe(false);
    expect(stillWritten(undefined, { group: S("A") }, "/v/a")).toBe(false);
  });
});
