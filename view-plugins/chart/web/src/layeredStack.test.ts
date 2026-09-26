/**
 * #847/#848 PR 5 P42 row 29 [user, 2026-09-26]: in a layered chart the stack
 * rule limits the stack layer only. Beside a layer that is not stacked, a
 * `keys:` / `highlight:` field the stack does not link by is accepted: the
 * unstacked layer writes and lights by it, the stack does neither (a brush
 * over a segment writes nothing; a marking on that field leaves it undimmed,
 * as any layer without the column), and the chart's note line says what the
 * stack links by (`sumNote`, the sandbox's `sum_note` word for word).
 *
 * The stack layer is the sandbox's real answer (`wire-corpus/stack-sums.json`,
 * "an upright bar of rows": x item, y value, colour group); the points beside
 * it are the same eight rows, unsummed.
 */
import { describe, expect, it } from "vitest";

import { isLit } from "../../../../web/src/lib/markings";
import { markedCount, markingLit, selectionMarking } from "./marking";
import { measuredFields, toOption } from "./option";
import { stackCase } from "./stackCorpus";
import { answer, cat, f64, layer } from "./testAnswer";

const c = stackCase("an upright bar of rows");
const stackAnswer = c.answer.layers[0]!;
const points = layer("scatter", 8, {
  item: cat(c.data.item as string[]),
  value: f64(c.data.value as number[]),
  region: cat(["n", "n", "s", "n", "s", "s", "n", "s"]),
});
const both = answer(stackAnswer, points);

const spec = (top: Record<string, unknown>) => ({
  view: "chart",
  source: "data/a.csv",
  ...top,
  layer: [
    { mark: { type: "bar", stack: true }, encoding: c.spec.encoding },
    {
      mark: "scatter",
      encoding: {
        x: { field: "item", type: "nominal" },
        y: { field: "value", type: "quantitative" },
        tooltip: { field: "region", type: "nominal" },
      },
    },
  ],
});

const NOTE = "the stack links by item, group only (each a sum)";

describe("a stack beside an unstacked layer (P42 row 29)", () => {
  it("says what the stack links by when the chart names a field it does not", () => {
    expect(toOption(spec({ keys: ["region"] }), both).notes).toEqual([NOTE]);
    expect(toOption(spec({ keys: ["item", "region"] }), both).notes).toEqual([NOTE]);
    expect(toOption(spec({ highlight: { where: "region == 'n'" } }), both).notes).toEqual([NOTE]);
    expect(toOption(spec({ highlight: { values: { region: ["n"] } } }), both).notes).toEqual([NOTE]);
  });

  it("says it once for two stacks that link alike", () => {
    const twice = spec({ keys: ["region"] });
    twice.layer.unshift(twice.layer[0]!);
    expect(toOption(twice, answer(stackAnswer, stackAnswer, points)).notes).toEqual([NOTE]);
  });

  it("says nothing when the chart names only what the stack links by", () => {
    expect(toOption(spec({ keys: ["item", "group"] }), both).notes).toEqual([]);
    expect(toOption(spec({ highlight: { where: "value > 10 and group == 'a'" } }), both).notes).toEqual([]);
    expect(toOption(spec({}), both).notes).toEqual([]);
  });

  it("names the op a mean stack's segments are", () => {
    const mean = spec({ keys: ["region"] });
    const enc = { ...(c.spec.encoding as Record<string, unknown>), y: { field: "value", type: "quantitative", aggregate: "mean" } };
    (mean.layer[0] as { encoding: unknown }).encoding = enc;
    expect(toOption(mean, both).notes).toEqual(["the stack links by item, group only (each a mean)"]);
  });

  it("writes, from a selection over both layers, only the unstacked layer's rows", () => {
    const measured = measuredFields(both);
    const mixed = [
      { source: "brush" as const, layer: 0, rows: [0, 1, 2] },
      { source: "brush" as const, layer: 1, rows: [2, 4] },
    ];
    const wrote = selectionMarking(mixed, both, ["region"], measured);
    expect(Object.fromEntries(Object.entries(wrote ?? {}).map(([k, v]) => [k, [...v]]))).toEqual({ region: ["s"] });
  });

  // P43: the demo's box over group b's points and a segment said "7 selected ·
  // by item" while the marking held 6 items: the segment wrote nothing. What
  // is counted beside "by <columns>" is what went to the marking (P36 row 10).
  it("counts, of a selection over both layers, only the rows that went to the marking", () => {
    const measured = measuredFields(both);
    const mixed = [
      { source: "brush" as const, layer: 0, rows: [0, 1, 2] },
      { source: "brush" as const, layer: 1, rows: [2, 4] },
    ];
    expect(markedCount(mixed, both, ["region"], measured)).toBe(2);
  });

  it("(control) counts every row when every selected layer writes", () => {
    const measured = measuredFields(both);
    expect(markedCount([{ source: "brush", layer: 1, rows: [0, 1, 2] }], both, ["region"], measured)).toBe(3);
  });

  it("writes nothing from a selection over the stack alone", () => {
    const measured = measuredFields(both);
    expect(selectionMarking([{ source: "brush", layer: 0, rows: [0, 1] }], both, ["region"], measured)).toBeNull();
  });

  it("leaves the stack undimmed under a marking on that field, as a layer without it", () => {
    const [onStack, onPoints] = markingLit(both, { region: new Set(["n"]) }, isLit, measuredFields(both));
    expect(onStack).toBeNull();
    expect(onPoints).toEqual([true, true, false, true, false, false, true, false]);
  });
});
