/**
 * #847/#848 P14: a zoned time shows in its column's zone, on the axis and in
 * the tooltip, and the zone is named; a zone-less one shows as written (UTC).
 * Neither follows the viewer's zone: the option is run under `useUTC`, with
 * each point at its wall time.
 */
import { describe, expect, it } from "vitest";

import { toOption } from "./option";
import { answer, base, f64, layer, q8 } from "./testAnswer";
import type { WireColumn } from "./wire";

const at = (iso: string) => Date.parse(iso);
const zoned = (isos: string[], zone: string): WireColumn => ({ ...(f64(isos.map(at)) as { data: string }), kind: "time", zone });
const plain = (isos: string[]): WireColumn => ({ ...(f64(isos.map(at)) as { data: string }), kind: "time" });

// Taipei midnight on 1 and 2 March, as instants
const TAIPEI = ["2026-02-28T16:00:00Z", "2026-03-01T16:00:00Z"];
const scatter = (x: WireColumn) => ({
  doc: {
    ...base,
    mark: "scatter",
    encoding: { x: { field: "at", type: "temporal" }, y: { field: "v", type: "quantitative" } },
  },
  a: answer(layer("scatter", 2, { at: x, v: f64([1, 2]) })),
});

type Series = { data: (number | null)[][] };
type Tip = { formatter: (p: { seriesIndex: number; dataIndex: number }) => string };

describe("a zoned time column", () => {
  it("is placed at its wall time on a UTC axis, and the axis names the zone", () => {
    const { doc, a } = scatter(zoned(TAIPEI, "Asia/Taipei"));
    const { option } = toOption(doc, a);
    expect(option.useUTC).toBe(true);
    const [s] = option.series as Series[];
    expect(s.data.map((p) => p[0])).toEqual([at("2026-03-01T00:00:00Z"), at("2026-03-02T00:00:00Z")]);
    expect((option.xAxis as { name: string }[])[0].name).toBe("at (Asia/Taipei)");
  });

  it("shows its wall time in the tooltip, with the zone", () => {
    const { doc, a } = scatter(zoned(TAIPEI, "Asia/Taipei"));
    const tip = toOption(doc, a).option.tooltip as Tip;
    expect(tip.formatter({ seriesIndex: 0, dataIndex: 0 })).toContain("at: <b>2026-03-01 Asia/Taipei</b>");
  });

  it("places a rule's datum in the same frame as its points", () => {
    const doc = {
      ...base,
      layer: [
        { mark: "scatter", encoding: { x: { field: "at", type: "temporal" }, y: { field: "v", type: "quantitative" } } },
        { mark: "rule", encoding: { x: { datum: "2026-03-01T12:00:00+08:00" } } },
      ],
    };
    const a = answer(layer("scatter", 2, { at: zoned(TAIPEI, "Asia/Taipei"), v: f64([1, 2]) }), layer("rule", 0, {}));
    const { option } = toOption(doc, a);
    const rule = (option.series as { markLine?: { data: { xAxis: number }[] } }[])[1];
    // noon in Taipei, on the axis where Taipei's midnight is 00:00
    expect(rule.markLine!.data[0].xAxis).toBe(at("2026-03-01T12:00:00Z"));
  });

  it("labels a temporal grid's cells in the zone", () => {
    const doc = {
      ...base,
      mark: "grid",
      encoding: {
        x: { field: "at", type: "temporal" },
        y: { field: "y", type: "ordinal" },
        color: { field: "v", type: "quantitative" },
      },
    };
    const a = answer(layer("grid", 2, { at: zoned(TAIPEI, "Asia/Taipei"), y: f64([0, 0]), v: q8([0, 254], 0, 1) }));
    const { option } = toOption(doc, a, { gridImage: () => ({}) });
    const x = (option.xAxis as { name: string; axisLabel: { formatter: (i: number) => string } }[])[0];
    expect([x.axisLabel.formatter(0), x.axisLabel.formatter(1)]).toEqual(["2026-03-01", "2026-03-02"]);
    expect(x.name).toBe("at (Asia/Taipei)");
  });
});

describe("a time column whose values have a time of day (#847/#848 PR 5 P24)", () => {
  // Seen live: an hourly zoned column's tooltip read "2026-03-01 Asia/Taipei"
  // at Taipei midnight, while 01:00 read "2026-03-01 01:00 Asia/Taipei"
  const HOURLY = ["2026-02-28T16:00:00Z", "2026-02-28T17:00:00Z"];

  it("shows the time of day in the tooltip at midnight too", () => {
    const { doc, a } = scatter(zoned(HOURLY, "Asia/Taipei"));
    const tip = toOption(doc, a).option.tooltip as Tip;
    expect(tip.formatter({ seriesIndex: 0, dataIndex: 0 })).toContain("at: <b>2026-03-01 00:00 Asia/Taipei</b>");
    expect(tip.formatter({ seriesIndex: 0, dataIndex: 1 })).toContain("at: <b>2026-03-01 01:00 Asia/Taipei</b>");
  });

  it("keeps a date column's dates when a value is missing", () => {
    // a missing time is no instant: read as 0 it would be 08:00 in Taipei
    const col: WireColumn = { ...(f64([at(TAIPEI[0]), null]) as { data: string }), kind: "time", zone: "Asia/Taipei" };
    const { doc } = scatter(col);
    const a = answer(layer("scatter", 2, { at: col, v: f64([1, 2]) }));
    const tip = toOption(doc, a).option.tooltip as Tip;
    expect(tip.formatter({ seriesIndex: 0, dataIndex: 0 })).toContain("at: <b>2026-03-01 Asia/Taipei</b>");
  });

  it("does so for a zone-less column too", () => {
    const { doc, a } = scatter(plain(["2026-03-01T00:00:00Z", "2026-03-01T00:00:30Z"]));
    const tip = toOption(doc, a).option.tooltip as Tip;
    expect(tip.formatter({ seriesIndex: 0, dataIndex: 0 })).toContain("at: <b>2026-03-01 00:00:00</b>");
  });

  it("labels a temporal grid's cells with the time of day", () => {
    const doc = {
      ...base,
      mark: "grid",
      encoding: {
        x: { field: "at", type: "temporal" },
        y: { field: "y", type: "ordinal" },
        color: { field: "v", type: "quantitative" },
      },
    };
    const a = answer(layer("grid", 2, { at: zoned(HOURLY, "Asia/Taipei"), y: f64([0, 0]), v: q8([0, 254], 0, 1) }));
    const { option } = toOption(doc, a, { gridImage: () => ({}) });
    const x = (option.xAxis as { axisLabel: { formatter: (i: number) => string } }[])[0];
    expect([x.axisLabel.formatter(0), x.axisLabel.formatter(1)]).toEqual(["2026-03-01 00:00", "2026-03-01 01:00"]);
  });
});

describe("a zone-less time column", () => {
  it("is placed as written and names no zone", () => {
    const { doc, a } = scatter(plain(["2026-03-01T00:00:00Z", "2026-03-02T00:00:00Z"]));
    const { option } = toOption(doc, a);
    expect(option.useUTC).toBe(true);
    const [s] = option.series as Series[];
    expect(s.data.map((p) => p[0])).toEqual([at("2026-03-01T00:00:00Z"), at("2026-03-02T00:00:00Z")]);
    expect((option.xAxis as { name: string }[])[0].name).toBe("at");
    const tip = option.tooltip as Tip;
    expect(tip.formatter({ seriesIndex: 0, dataIndex: 0 })).toContain("at: <b>2026-03-01</b>");
  });
});
