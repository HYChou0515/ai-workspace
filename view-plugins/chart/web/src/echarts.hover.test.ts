/**
 * #847/#848 PR 5 P25 against REAL ECharts (SSR, SVG): hovering a line finds
 * its points. Found live: the tooltip is an item tooltip, a point is the item,
 * and a line drew no points (`showSymbol: false`), so hovering a line showed
 * nothing. Its points are now there to hover, drawn only when hovered.
 */
import * as echarts from "echarts/core";
import { BrushComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { toOption } from "./option";
import { type BrushSelected, selectionFromBrush } from "./selection";
import { answer, base, f64, layer } from "./testAnswer";

echarts.use([SVGRenderer, BrushComponent]);

const XS = [1, 2, 3, 4, 5];
const YS = [3, 1, 4, 1, 5];

/** What the pointer finds at each point of layer 0, as ECharts reports it. */
function hovered(mark: string | object): (number | undefined)[] {
  const doc = {
    ...base,
    mark,
    encoding: { x: { field: "x", type: "quantitative" }, y: { field: "y", type: "quantitative" } },
  };
  const built = toOption(doc, answer(layer("line", XS.length, { x: f64(XS), y: f64(YS) })));
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
  chart.setOption(built.option, true);
  const found: (number | undefined)[] = [];
  const zr = chart.getZr();
  XS.forEach((x, i) => {
    const [px, py] = chart.convertToPixel({ seriesIndex: 0 }, [x, YS[i]]) as number[];
    let seen: number | undefined;
    const listen = (p: { dataIndex?: number }) => {
      seen = p.dataIndex;
    };
    chart.on("mouseover", listen);
    zr.handler.dispatch("mousemove", { zrX: px, zrY: py } as never);
    chart.off("mouseover", listen);
    zr.handler.dispatch("mousemove", { zrX: 1, zrY: 1 } as never); // leave, so the next is a new hover
    found.push(seen);
  });
  chart.dispose();
  return found;
}

describe("brushing a chart with a line, against real ECharts", () => {
  it("still selects the points in the box (a pin)", async () => {
    // (ECharts' line series has no brush selector of its own, before this
    // change or after: a brush selects a scatter's points, not a line's)
    const xy = { x: { field: "x", type: "quantitative" }, y: { field: "y", type: "quantitative" } };
    const doc = { ...base, layer: [{ mark: "scatter", encoding: xy }, { mark: "line", encoding: xy }] };
    const built = toOption(
      doc,
      answer(
        layer("scatter", XS.length, { x: f64(XS), y: f64(YS) }),
        layer("line", XS.length, { x: f64(XS), y: f64(YS) }),
      ),
    );
    const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
    const events: BrushSelected[] = [];
    chart.on("brushselected", (e) => {
      events.push(e as BrushSelected);
    });
    chart.setOption(built.option, true);
    chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", xAxisIndex: 0, coordRange: [[1.5, 3.5], [0, 5]] }] });
    await new Promise((r) => setTimeout(r, 400)); // the option debounces brush events 250 ms
    expect(selectionFromBrush(events.at(-1) as BrushSelected, built)).toEqual([{ source: "brush", layer: 0, rows: [1, 2] }]);
    chart.dispose();
  });
});

describe("hovering a line, against real ECharts", () => {
  it("(control) a scatter's points are found where they are drawn", () => {
    expect(hovered("scatter")).toEqual([0, 1, 2, 3, 4]);
  });

  it("finds each point of a line, so its tooltip shows that point's values", () => {
    expect(hovered("line")).toEqual([0, 1, 2, 3, 4]);
  });

  it("does so for an area and a smoothed line, and a line that asks for no points", () => {
    expect(hovered("area")).toEqual([0, 1, 2, 3, 4]);
    expect(hovered({ type: "line", smooth: true })).toEqual([0, 1, 2, 3, 4]);
    expect(hovered({ type: "line", point: false })).toEqual([0, 1, 2, 3, 4]);
  });
});
