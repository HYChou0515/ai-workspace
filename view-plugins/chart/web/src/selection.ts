/**
 * What the person selected on a chart, in LAYER ROWS.
 *
 * ECharts reports selections per series and data index; `Built.series` maps
 * those back to the rows the sandbox's answer holds, so every gesture speaks
 * one language. PR 3 turns a `Selection` into a marking write through
 * `selectionValues` (the `keys:` columns' values over the selected rows, as
 * the marking strings `canon` makes). In this PR a selection is local to the
 * view.
 *
 * - brush (`rect`) and lasso (`polygon`): the points ECharts found inside. A
 *   grid is one raster image with no points, so for it the cells whose centre
 *   lies inside the area are found here (`Cells.rowAt`).
 * - legend: ECharts' own show / hide stays; with a category hidden, the rows
 *   still shown are the selection; with all shown, nothing is.
 */
import type { Answer, Built } from "./option";
import { canon, decodeColumn } from "./wire";

export type Selection = { source: "brush" | "lasso" | "legend"; layer: number; rows: number[] };

type BrushArea = { brushType: string; coordRange?: unknown };
export type BrushSelected = {
  areas: BrushArea[];
  batch: { selected: { seriesIndex: number; dataIndex: number[] }[] }[];
};

function group(source: Selection["source"], pairs: [number, number][]): Selection[] {
  const byLayer = new Map<number, Set<number>>();
  for (const [layer, row] of pairs) {
    const rows = byLayer.get(layer) ?? new Set<number>();
    rows.add(row);
    byLayer.set(layer, rows);
  }
  return [...byLayer.entries()]
    .sort(([a], [b]) => a - b)
    .map(([layer, rows]) => ({ source, layer, rows: [...rows].sort((x, y) => x - y) }));
}

function inside(point: [number, number], area: BrushArea): boolean {
  const [x, y] = point;
  if (area.brushType === "rect") {
    const [[x0, x1], [y0, y1]] = area.coordRange as [[number, number], [number, number]];
    return x >= Math.min(x0, x1) && x <= Math.max(x0, x1) && y >= Math.min(y0, y1) && y <= Math.max(y0, y1);
  }
  // Ray casting over the polygon's vertices.
  const poly = area.coordRange as [number, number][];
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

export function selectionFromBrush(event: BrushSelected, built: Built): Selection[] {
  if (event.areas.length === 0) return [];
  const source = event.areas.some((a) => a.brushType === "polygon") ? "lasso" : "brush";
  const pairs: [number, number][] = [];
  for (const s of event.batch[0]?.selected ?? []) {
    const map = built.series[s.seriesIndex];
    if (!map) continue;
    for (const i of s.dataIndex) if (map.rows[i] !== undefined) pairs.push([map.layer, map.rows[i]]);
  }
  for (const g of built.grids) {
    const cells = g.cells;
    for (let r = 0; r < cells.height; r++) {
      for (let c = 0; c < cells.width; c++) {
        const row = cells.rowAt(c, r);
        // Axis values are cell indices; row r from the top is y index height-1-r.
        if (row >= 0 && event.areas.some((a) => a.coordRange && inside([c, cells.height - 1 - r], a))) {
          pairs.push([g.layer, row]);
        }
      }
    }
  }
  return group(source, pairs);
}

export function selectionFromLegend(selected: Record<string, boolean>, built: Built): Selection[] {
  if (Object.values(selected).every(Boolean)) return [];
  const pairs: [number, number][] = [];
  built.series.forEach((s, i) => {
    const name = built.names[i];
    if (name !== undefined && selected[name] !== false) for (const r of s.rows) pairs.push([s.layer, r]);
  });
  return group("legend", pairs);
}

/** The `keys:` columns' values over the selected rows — what a marking holds. */
export function selectionValues(sel: Selection, answer: Answer, keys: string[]): Record<string, string[]> {
  const columns = answer.layers[sel.layer]?.columns ?? {};
  const out: Record<string, string[]> = {};
  for (const key of keys) {
    const wire = columns[key];
    if (!wire) continue;
    const col = decodeColumn(wire);
    const seen = new Set<string>();
    for (const r of sel.rows) {
      const text = canon(col.value(r));
      if (text !== null) seen.add(text);
    }
    out[key] = [...seen];
  }
  return out;
}
