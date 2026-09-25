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
import { type Answer, type Built, type Measured, rowsAt, type WireLayer } from "./option";
import { litRows } from "./highlight";
import { canon, type Column, decodeColumn } from "./wire";

export type Selection = { source: "brush" | "lasso" | "legend" | "click"; layer: number; rows: number[] };

/** What ECharts hands a `click` listener, as far as a selection reads it. */
export type ClickParams = { seriesType?: string; seriesIndex?: number; dataIndex?: number };

/** A click on a pie's slice selects that slice's rows (#847/#848 PR 5 P30):
 * a brush has nothing to cover on a pie, so a slice is picked by clicking it.
 * Any other click selects nothing here. */
export function selectionFromClick(p: ClickParams, built: Built): Selection[] {
  if (p.seriesType !== "pie") return [];
  // a pie's slice j draws row j of its layer (a pie is never stacked)
  const map = built.series[p.seriesIndex as number]!;
  return [{ source: "click", layer: map.layer, rows: [map.rows[p.dataIndex as number] as number] }];
}

type BrushArea = { brushType: string; coordRange?: unknown };
/** ECharts' `brushselected` payload. The areas sit INSIDE each batch entry,
 * beside what they selected — not at the top (ECharts also fires this once
 * when a brush component is first set up, with no areas). */
export type BrushSelected = {
  batch: { areas?: BrushArea[]; selected?: { seriesIndex: number; dataIndex: number[] }[] }[];
};

/** The marks whose selection ECharts shows nothing of, so the view lights it:
 * a grid is one raster, which ECharts' brush styling cannot dim (#847/#848
 * P18); a pie's slice is picked by a click, which ECharts does not style
 * (PR 5 P30). */
const LIT_BY_SELECTION = new Set(["grid", "pie"]);

/** A selection as the lit rows of each layer of a `LIT_BY_SELECTION` mark it
 * took rows of -- a layer it took none of keeps the spec's highlight -- or
 * undefined when it took none of theirs. */
export function ownSelectionLit(answer: Answer, selection: Selection[]): (boolean[] | null)[] | undefined {
  const taken = new Map<number, Set<number>>();
  for (const s of selection) {
    if (!LIT_BY_SELECTION.has(answer.layers[s.layer]?.mark as string)) continue;
    const rows = taken.get(s.layer) ?? new Set<number>();
    for (const r of s.rows) rows.add(r);
    taken.set(s.layer, rows);
  }
  if (taken.size === 0) return undefined;
  return answer.layers.map((ly, i) => {
    const rows = taken.get(i);
    return rows ? Array.from({ length: ly.rows }, (_, r) => rows.has(r)) : litRows(ly);
  });
}

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
  const areas = event.batch?.[0]?.areas ?? [];
  if (areas.length === 0) return [];
  const source = areas.some((a) => a.brushType === "polygon") ? "lasso" : "brush";
  const pairs: [number, number][] = [];
  for (const s of event.batch[0]?.selected ?? []) {
    const map = built.series[s.seriesIndex];
    if (!map) continue;
    // every row a point stands for: a stack's sum stands for all of its rows
    // (P37 row 12), a point a stack added to line it up for none
    for (const i of s.dataIndex) for (const row of rowsAt(map.rows[i])) pairs.push([map.layer, row]);
  }
  for (const g of built.grids) {
    const cells = g.cells;
    for (let r = 0; r < cells.height; r++) {
      for (let c = 0; c < cells.width; c++) {
        const row = cells.rowAt(c, r);
        // Axis values are cell indices; row r from the top is y index height-1-r.
        if (row >= 0 && areas.some((a) => a.coordRange && inside([c, cells.height - 1 - r], a))) {
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
    const slices = built.slices[i];
    if (slices) {
      // A pie's legend entries are its slices, one per row (a pie is never
      // stacked, so every slice draws one).
      s.rows.forEach((r, j) => selected[slices[j]] !== false && pairs.push([s.layer, r as number]));
      return;
    }
    const name = built.names[i];
    if (name !== undefined && selected[name] !== false) for (const entry of s.rows) for (const r of rowsAt(entry)) pairs.push([s.layer, r]);
  });
  return group("legend", pairs);
}

/** Where a key's MARKING strings live in a layer: `$key.<name>` when a channel
 * sends the key as numbers / time / q8 (query.py adds it), else the key's own
 * column. The one lookup for everything that compares a layer with a marking —
 * writing one (`selectionValues`) and lighting by one — so the two never read
 * different columns. Null when the layer does not carry the key — nor does a
 * binned layer: its rows are bins, its columns bin centres, never a row's value
 * — nor a column a channel aggregates (`measured`, the layer's
 * `measuredFields`; #847/#848 PR 5 P32): it holds counts or means under the
 * field's name, never the field's values. */
export function keyColumn(layer: WireLayer | undefined, key: string, measured: ReadonlySet<string>): Column | null {
  if (layer?.binned || measured.has(key)) return null;
  const wire = layer?.columns[`$key.${key}`] ?? layer?.columns[key];
  return wire ? decodeColumn(wire) : null;
}

const NONE_MEASURED: ReadonlySet<string> = new Set();

/** The `keys:` columns' values over the selected rows — what a marking holds. */
export function selectionValues(sel: Selection, answer: Answer, keys: string[], measured: Measured): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const key of keys) {
    const col = keyColumn(answer.layers[sel.layer], key, measured[sel.layer] ?? NONE_MEASURED);
    if (!col) continue;
    const seen = new Set<string>();
    for (const r of sel.rows) {
      const text = canon(col.value(r));
      if (text !== null) seen.add(text);
    }
    out[key] = [...seen];
  }
  return out;
}
