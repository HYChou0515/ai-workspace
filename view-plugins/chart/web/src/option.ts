/**
 * A chart spec plus the sandbox's answer → an ECharts option. Pure: it builds
 * plain objects and closures and never touches a chart instance or the DOM, so
 * the renderer (P6) only mounts it and wires events.
 *
 * Besides the option it returns `series`: for series i, `rows[j]` is the layer
 * row that data point j draws. A brush, a lasso and a legend click all speak
 * in layer rows, so this table is how each maps from what ECharts reports
 * (dataIndex per seriesIndex). A highlight is drawn per layer row too: an unlit
 * row's data item is dimmed where it is built (`item()`).
 *
 * Category axes carry INDICES in the data, never the labels: ECharts reads a
 * number on a category axis as an index, so a category whose labels are
 * numbers would otherwise land on the wrong tick.
 */
import { clockFor, type Clock, type Precision } from "./clock";
import { DIM_OPACITY, litRows } from "./highlight";
import { CATEGORY_COLOURS, categoryTable, colourTable, lattice, paintCells, type Cells, type RasterImage } from "./raster";

export { CATEGORY_COLOURS };
import { decodeColumn, type Column, type Scalar, type WireColumn } from "./wire";
import schema from "../../sandbox-src/src/chart_view/spec.schema.json";

export type WireLayer = {
  mark: string;
  rows: number;
  columns: Record<string, WireColumn>;
  highlight: string | null;
  lit: number | null;
  binned: { points: number; bins: number } | null;
  outliers: { rows: number; columns: Record<string, WireColumn> } | null;
};

export type Answer = { format: 1; layers: WireLayer[] };

type Channel = {
  field?: string;
  type?: "quantitative" | "nominal" | "ordinal" | "temporal";
  datum?: number | string;
  aggregate?: string;
  title?: string;
  sort?: "ascending" | "descending" | Scalar[];
  scale?: { type?: "linear" | "log"; zero?: boolean; domain?: Scalar[]; scheme?: "sequential" | "diverging" };
};

type Encoding = Partial<Record<"x" | "y" | "x2" | "y2" | "color" | "size" | "theta" | "text", Channel>> & {
  tooltip?: Channel | Channel[];
};

type MarkDef = {
  type: string;
  color?: string;
  opacity?: number;
  point?: boolean;
  smooth?: boolean;
  stack?: boolean;
  extent?: string;
};

type LayerSpec = { mark: string | MarkDef; encoding: Encoding };

export type ChartSpec = Record<string, unknown> & {
  mark?: string | MarkDef;
  encoding?: Encoding;
  layer?: LayerSpec[];
};

export type SeriesRows = { layer: number; rows: number[] };

export type GridLayer = { layer: number; seriesIndex: number; cells: Cells; image: RasterImage };

export type Built = {
  option: Record<string, unknown>;
  series: SeriesRows[];
  /** Series i's legend name, when it has one (a nominal colour's level). */
  names: (string | undefined)[];
  /** A pie's legend names its SLICES: slice j of series i is `slices[i][j]`. */
  slices: (string[] | undefined)[];
  grids: GridLayer[];
  notes: string[];
};

export type Options = {
  /** Turn a grid's pixels into something ECharts can draw (a canvas, in the
   * browser). Omitted in a host-free test, where the series draws nothing. */
  gridImage?: (grid: { cells: Cells; image: RasterImage }) => unknown;
  /** #847 PR 3: per layer, the rows a NAMED MARKING lights, replacing the
   * spec's own `highlight:` bitset (`null` = this layer is not linked, drawn
   * undimmed). Omitted: the spec's highlight, as before. */
  lit?: (boolean[] | null)[];
};

const NONE = "(none)";
const PALETTE_STOPS = 9;

function layersOf(spec: ChartSpec): LayerSpec[] {
  if (spec.layer) return spec.layer;
  return [{ mark: spec.mark as string | MarkDef, encoding: spec.encoding as Encoding }];
}

function markOf(layer: LayerSpec): MarkDef {
  return typeof layer.mark === "string" ? { type: layer.mark } : layer.mark;
}

function escape(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/** One clock per zone: building an Intl formatter per tooltip is slow. */
const clocks = new Map<string, Clock>();
function clockOf(zone: string | undefined): Clock {
  const key = zone ?? "";
  let clock = clocks.get(key);
  if (!clock) clocks.set(key, (clock = clockFor(zone)));
  return clock;
}

/** A time as its column shows it (#847/#848 P14): the wall time in its zone,
 * named, or as written for a zone-less one; to the finest part the column
 * has (P24), so an hourly column's midnight reads 00:00. */
function showTime(clock: Clock, ms: number, at: Precision): string {
  return clock.zone ? `${clock.text(ms, at)} ${clock.zone}` : clock.text(ms, at);
}

/** A time column's precision, read once per decoded column. */
const precisions = new WeakMap<Column, Precision>();
function columnPrecision(col: Column, clock: Clock): Precision {
  let at = precisions.get(col);
  if (at === undefined) {
    const instants = Array.from({ length: col.length }, (_, i) => col.value(i)).filter((v) => typeof v === "number");
    precisions.set(col, (at = clock.precision(instants)));
  }
  return at;
}

function show(col: Column, row: number): string {
  const v = col.value(row);
  if (v === null) return "—";
  if (col.kind === "time") {
    const clock = clockOf(col.zone);
    return showTime(clock, v as number, columnPrecision(col, clock));
  }
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(6)));
  return String(v);
}

function hex(table: Uint8ClampedArray, code: number): string {
  const c = code * 4;
  return `#${[table[c], table[c + 1], table[c + 2]].map((n) => n.toString(16).padStart(2, "0")).join("")}`;
}

/** #847/#848 P16: the decimals a colour scale's labels need -- its width to two
 * significant digits (0.056–0.346 → 2, 12–480 → 0), or a constant's own
 * value to three. ECharts' default of 0 labelled 0.056–0.346 "0" at both ends. */
export function scalePrecision(lo: number, hi: number): number {
  const span = Math.abs(hi - lo);
  const [size, digits] = span > 0 ? [span, 1] : [Math.abs(lo), 2];
  if (!(size > 0) || !Number.isFinite(size)) return 0;
  return Math.min(20, Math.max(0, digits - Math.floor(Math.log10(size))));
}

/** A continuous visualMap's labels: readable ends (see `scalePrecision`). One
 * the person cannot drag (a grid's) draws no handle values, so it is given
 * its ends as text. */
function scaleLabels(lo: number, hi: number, calculable: boolean): Record<string, unknown> {
  const precision = scalePrecision(lo, hi);
  return calculable ? { precision } : { precision, text: [hi.toFixed(precision), lo.toFixed(precision)] };
}

function palette(scheme: "sequential" | "diverging", min: number, max: number): string[] {
  const table = colourTable(scheme, min, max);
  return Array.from({ length: PALETTE_STOPS }, (_, i) => hex(table, Math.round((i / (PALETTE_STOPS - 1)) * 254)));
}

/** A category axis's labels: every level any layer has for `field`, in spec order. */
function categories(field: string, channel: Channel, decoded: Record<string, Column>[]): Scalar[] {
  const seen: Scalar[] = [];
  for (const cols of decoded) {
    const col = cols[field];
    if (!col) continue;
    for (let i = 0; i < col.length; i++) {
      const v = col.value(i);
      if (v !== null && !seen.includes(v)) seen.push(v);
    }
  }
  seen.sort((a, b) =>
    typeof a === "number" && typeof b === "number" ? a - b : String(a) < String(b) ? -1 : String(a) > String(b) ? 1 : 0,
  );
  if (channel.sort === "descending") seen.reverse();
  else if (Array.isArray(channel.sort)) {
    const first = channel.sort.filter((v) => seen.some((s) => String(s) === String(v)));
    const firstKeys = new Set(first.map(String));
    return [...first.map((v) => seen.find((s) => String(s) === String(v)) as Scalar), ...seen.filter((s) => !firstKeys.has(String(s)))];
  }
  return seen;
}

/** The forms a date datum may take — the schema's, which validate reads too. */
const INSTANT = new RegExp(schema.$defs.instant.pattern);

/** Epoch ms for a date datum, or NaN where the chart places none — exactly
 * where the sandbox's `datums.instant_ms` gives None, which validate
 * refuses (held to `wire-corpus/instants.json`, written from it). The text is
 * one of the schema's `$defs.instant` forms, UTC unless it names a zone, and
 * a day the calendar lacks (2024-02-30) is NaN. It is read from the pattern's
 * groups, not with `Date.parse`: that read a zone-less time in the VIEWER's
 * zone, and each engine reads the rest its own way — V8 took a zoned
 * "0050-06-01 12:00Z" as 1950 and refused "2024/03/01T12:00Z". */
export function parseInstant(text: string): number {
  const m = INSTANT.exec(text);
  if (!m) return Number.NaN;
  const [year, month, day] = [Number(m[1]), Number(m[3]), Number(m[4])];
  // setUTCFullYear, not Date.UTC: that reads years 0-99 as 1900-1999.
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return Number.NaN;
  const [hour, minute, second] = [Number(m[5] ?? 0), Number(m[6] ?? 0), Number(m[7] ?? 0)];
  const fraction = m[8] ? Number(m[8].padEnd(3, "0")) : 0;
  let ms = date.getTime() + ((hour * 60 + minute) * 60 + second) * 1000 + fraction;
  const zone = m[9];
  if (zone && zone !== "Z") ms -= (zone[0] === "-" ? -1 : 1) * (Number(m[10]) * 60 + Number(m[11])) * 60_000;
  return ms;
}

type Axis = {
  channel: Channel;
  kind: "value" | "log" | "time" | "category" | "index";
  labels: Scalar[];
  /** The axis position of layer row `row` of a column. */
  at(col: Column, row: number): number | null;
  /** The axis position of one value (a rule's datum). */
  pos(v: Scalar | null): number | null;
  /** A temporal axis's clock (#847/#848 P14): its column's zone, if any. */
  clock: Clock | null;
};

/** The clock a temporal channel's times show in: the zone its column names,
 * in the first layer that sends it as time. */
function channelClock(channel: Channel | undefined, decoded: Record<string, Column>[]): Clock | null {
  if (channel?.type !== "temporal" || !channel.field) return null;
  const col = decoded.map((cols) => cols[channel.field as string]).find((c) => c?.kind === "time");
  return clockOf(col?.zone);
}

function axisFor(channel: Channel | undefined, decoded: Record<string, Column>[], grid: Cells | null, which: "x" | "y"): Axis | null {
  const clock = channelClock(channel, decoded);
  if (grid) {
    const labels = which === "x" ? grid.xs : grid.ys;
    // A cell's position is its index; a value finds its cell by label, and a
    // number between two numeric cells sits between their centres. Any layer
    // over the lattice (points marking cells, a threshold) is placed this way.
    const cell = new Map(labels.map((v, i) => [String(v), i]));
    const temporal = channel?.type === "temporal";
    const pos = (value: Scalar | null): number | null => {
      // A temporal grid's cells are epoch ms: a text datum is a date first.
      // On a number grid a datum is a number, as on any number axis.
      if (channel?.type === "quantitative" && typeof value === "string") return null;
      const v = temporal && typeof value === "string" ? parseInstant(value) : value;
      if (v === null || Number.isNaN(v)) return null;
      const exact = cell.get(String(v));
      if (exact !== undefined || typeof v !== "number") return exact ?? null;
      for (let i = 0; i + 1 < labels.length; i++) {
        const a = labels[i];
        const b = labels[i + 1];
        if (typeof a === "number" && typeof b === "number" && (a - v) * (b - v) < 0) return i + (v - a) / (b - a);
      }
      return null;
    };
    return { channel: channel ?? {}, kind: "index", labels, at: (col, row) => pos(col.value(row)), pos, clock };
  }
  if (!channel?.field) return null;
  if (channel.type === "nominal" || channel.type === "ordinal") {
    const labels = categories(channel.field, channel, decoded);
    const index = new Map(labels.map((v, i) => [String(v), i]));
    return {
      channel,
      kind: "category",
      labels,
      at: (col, row) => {
        const v = col.value(row);
        return v === null ? null : (index.get(String(v)) ?? null);
      },
      pos: (v) => (v === null ? null : (index.get(String(v)) ?? null)),
      clock: null,
    };
  }
  const kind = channel.type === "temporal" ? "time" : channel.scale?.type === "log" ? "log" : "value";
  // A time axis runs under `useUTC` with each instant at its WALL time, so
  // ECharts' ticks and labels read the column's clock, not the viewer's.
  const wall = (v: number) => (clock ? clock.wall(v) : v);
  const pos = (v: Scalar | null): number | null => {
    const n = kind === "time" && typeof v === "string" ? parseInstant(v) : v;
    // Text on a number axis, or 0 and below on a log one, has no position.
    if (typeof n !== "number" || !Number.isFinite(n) || (kind === "log" && n <= 0)) return null;
    return kind === "time" ? wall(n) : n;
  };
  // A mark's points go as the layer holds them, for ECharts to read (text
  // from a layer that typed the field otherwise included); only a rule's
  // own values are held to `pos`.
  const at = (col: Column, row: number): number | null => {
    const v = col.value(row) as number | null;
    return kind === "time" && col.kind === "time" && v !== null ? wall(v) : v;
  };
  return { channel, kind, labels: [], at, pos, clock };
}

/** Where an axis name goes: centred beside its axis. At ECharts' default (the
 * axis end) a long field name ran past the plot and was cut off. */
const NAME_AT = { x: { nameLocation: "middle", nameGap: 28 }, y: { nameLocation: "middle", nameGap: 44 } };

/** A number, log, time or grid axis leaves out a label that would overlap its
 * neighbour: on a narrow chart a time axis read "Mar06:0012:00…" (#847/#848
 * P14) and a number axis "98100102104106" (P24, at 390 px). */
const LABELS = { hideOverlap: true, padding: [0, 3], backgroundColor: "transparent" };

function axisOption(
  axis: Axis | null,
  grid: Cells | null,
  which: "x" | "y",
  zeroByDefault: boolean,
): Record<string, unknown> {
  if (!axis) return { type: "value" };
  const field = axis.channel.title ?? axis.channel.field;
  // a zoned time axis names its zone (#847/#848 P14)
  const name = axis.clock?.zone && field !== undefined ? `${field} (${axis.clock.zone})` : field;
  const clock = axis.clock;
  if (axis.kind === "index" && grid) {
    const n = which === "x" ? grid.width : grid.height;
    // Ticks and labels at the cell CENTRES: left to itself the axis ticks at
    // -0.5, 0.5, … (its min plus the interval), the cell edges, where no label
    // belongs — every label came out blank.
    const centres = Array.from({ length: n }, (_, i) => i);
    // every cell to the finest part any has (P24): 00:00 on an hourly lattice
    // (a temporal lattice's cells are all epoch ms; the filter types them)
    const at = clock ? clock.precision(axis.labels.filter((v): v is number => typeof v === "number")) : "day";
    return {
      type: "value",
      name,
      ...NAME_AT[which],
      min: -0.5,
      max: n - 0.5,
      // on the lattice's edge: by default an axis crosses the other at its 0,
      // which on an index axis is the first cell's CENTRE (#847/#848 P18)
      axisLine: { onZero: false },
      splitLine: { show: false },
      axisTick: { customValues: centres },
      axisLabel: {
        ...LABELS,
        customValues: centres,
        // a temporal cell is epoch ms: shown on its column's clock
        formatter: (i: number) => {
          const label = axis.labels[i];
          return clock && typeof label === "number" ? clock.text(label, at) : String(label ?? "");
        },
      },
    };
  }
  // a category axis already leaves out labels by its own interval (pinned in
  // echarts.overlap.test.ts)
  if (axis.kind === "category") return { type: "category", name, ...NAME_AT[which], data: axis.labels.map(String) };
  const out: Record<string, unknown> = { type: axis.kind, name, ...NAME_AT[which], axisLabel: LABELS };
  // Zero is in the domain only where a mark's LENGTH is its value (bar, area)
  // or the spec asks for it: a scatter of values near 100 squeezed against a
  // zero it never reaches hides the very spread it was drawn to show.
  if (axis.kind !== "time" && !(axis.channel.scale?.zero ?? zeroByDefault)) out.scale = true;
  const domain = axis.channel.scale?.domain;
  if (domain && typeof domain[0] === "number") {
    out.min = domain[0];
    out.max = domain[domain.length - 1];
  }
  return out;
}

function tooltipChannels(enc: Encoding): Channel[] {
  const tips = enc.tooltip === undefined ? [] : Array.isArray(enc.tooltip) ? enc.tooltip : [enc.tooltip];
  const all = [enc.x, enc.y, enc.x2, enc.y2, enc.color, enc.size, enc.theta, enc.text, ...tips];
  const seen = new Set<string>();
  return all.filter((c): c is Channel => {
    if (!c?.field || seen.has(c.field)) return false;
    seen.add(c.field);
    return true;
  });
}

function range(col: Column): [number, number] {
  let lo = Number.POSITIVE_INFINITY;
  let hi = Number.NEGATIVE_INFINITY;
  for (let i = 0; i < col.length; i++) {
    const v = col.value(i);
    if (typeof v === "number") {
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }
  }
  return lo <= hi ? [lo, hi] : [0, 0];
}

/** `doc` is a chart document that already passed `specErrors`. */
export function toOption(doc: object, answer: Answer, opts: Options = {}): Built {
  const spec = doc as ChartSpec;
  const specs = layersOf(spec);
  const decoded = answer.layers.map((ly) =>
    Object.fromEntries(Object.entries(ly.columns).map(([k, w]) => [k, decodeColumn(w)])),
  );

  const series: Record<string, unknown>[] = [];
  const rows: SeriesRows[] = [];
  const names: (string | undefined)[] = [];
  const slices: (string[] | undefined)[] = [];
  const grids: GridLayer[] = [];
  const notes: string[] = [];
  const visualMaps: Record<string, unknown>[] = [];
  const legend: string[] = [];

  // One pair of axes for the whole chart: a grid's when a layer is a grid
  // (below), otherwise from the first layer that names each.
  const gridIndex = specs.findIndex((s) => markOf(s).type === "grid");
  let gridCells: Cells | null = null;
  if (gridIndex >= 0) {
    const enc = specs[gridIndex].encoding;
    const cols = decoded[gridIndex];
    const x = cols[enc.x?.field as string];
    const y = cols[enc.y?.field as string];
    const c = cols[enc.color?.field as string];
    const n = answer.layers[gridIndex].rows;
    gridCells = lattice(
      Array.from({ length: n }, (_, i) => x.value(i)),
      Array.from({ length: n }, (_, i) => y.value(i)),
      Array.from({ length: n }, (_, i) => (c?.code ? c.code(i) : 255)),
    );
  }
  // A grid's cells ARE the axes; otherwise the first layer with a field.
  const axisChannel = (c: "x" | "y") =>
    gridIndex >= 0 ? specs[gridIndex].encoding[c] : specs.map((s) => s.encoding[c]).find((ch) => ch?.field);
  const xChannel = axisChannel("x");
  const yChannel = axisChannel("y");
  const xAxis = axisFor(xChannel, decoded, gridCells, "x");
  const yAxis = axisFor(yChannel, decoded, gridCells, "y");
  const cartesian = specs.some((s) => markOf(s).type !== "pie");

  const point = (li: number, row: number, extra: (number | null)[] = []): (number | null)[] => {
    const enc = specs[li].encoding;
    const cols = decoded[li];
    const px = xAxis && enc.x?.field ? xAxis.at(cols[enc.x.field], row) : null;
    const py = yAxis && enc.y?.field ? yAxis.at(cols[enc.y.field], row) : null;
    return [px, py, ...extra];
  };
  // Boxplot / errorbar rows carry `$` summaries instead of the y field.
  const xAt = (li: number, row: number): number | null => {
    const f = specs[li].encoding.x?.field;
    return xAxis && f ? xAxis.at(decoded[li][f], row) : null;
  };

  const common = (mark: MarkDef) => ({
    emphasis: { focus: "self" },
    blur: { itemStyle: { opacity: 0.15 }, lineStyle: { opacity: 0.15 } },
    ...(mark.color ? { itemStyle: { color: mark.color } } : {}),
  });

  specs.forEach((ly, li) => {
    const mark = markOf(ly);
    const enc = ly.encoding;
    const cols = decoded[li];
    const wire = answer.layers[li];
    const n = wire.rows;
    const all = Array.from({ length: n }, (_, i) => i);
    // `highlight:` — an unlit row is drawn dimmed in its own data item.
    const lit = opts.lit ? (opts.lit[li] ?? null) : litRows(wire);
    const item = <T,>(value: T, row: number): T | { value: T; itemStyle: { opacity: number } } =>
      lit && !lit[row] ? { value, itemStyle: { opacity: DIM_OPACITY } } : value;
    const push = (s: Record<string, unknown>, r: number[]) => {
      series.push(s);
      rows.push({ layer: li, rows: r });
      names.push(typeof s.name === "string" ? s.name : undefined);
      slices.push(s.type === "pie" ? (s.data as { name: string }[]).map((d) => d.name) : undefined);
    };

    if (mark.type === "grid") {
      const cells = gridCells as Cells;
      const c = cols[enc.color?.field as string];
      const scheme = enc.color?.scale?.scheme ?? "sequential";
      // Categories are levels, not a range: each its own palette colour and a
      // legend naming them. Through the continuous ramp (a min and max they do
      // not have) every level came out one colour (#847/#848 P19).
      const levels = c?.kind === "cat" ? (c.levels ?? []) : null;
      const table = levels ? categoryTable(levels.length) : colourTable(scheme, c?.min ?? 0, c?.max ?? 0);
      const image = paintCells(cells, table, lit ?? undefined);
      const source = opts.gridImage?.({ cells, image });
      grids.push({ layer: li, seriesIndex: series.length, cells, image });
      visualMaps.push(
        levels
          ? {
              type: "piecewise",
              categories: levels.map(String),
              inRange: { color: levels.map((_, i) => CATEGORY_COLOURS[i % CATEGORY_COLOURS.length]) },
              selectedMode: false,
              seriesIndex: series.length,
            }
          : {
              type: "continuous",
              min: c?.min ?? 0,
              max: c?.max ?? 0,
              calculable: false,
              ...scaleLabels(c?.min ?? 0, c?.max ?? 0, false),
              seriesIndex: series.length,
              inRange: { color: palette(scheme, c?.min ?? 0, c?.max ?? 0) },
            },
      );
      push(
        {
          type: "custom",
          silent: true,
          data: [[0, 0]],
          renderItem: (_params: unknown, api: { coord: (p: number[]) => number[] }) => {
            if (!source) return null;
            const tl = api.coord([-0.5, cells.height - 0.5]);
            const br = api.coord([cells.width - 0.5, -0.5]);
            return { type: "image", style: { image: source, x: tl[0], y: tl[1], width: br[0] - tl[0], height: br[1] - tl[1] } };
          },
        },
        [],
      );
      return;
    }

    if (mark.type === "rule") {
      // The label inside the plot, above the line's end: at ECharts' default
      // (past the end) it ran into the 16 px margin and was cut -- "102" drew
      // as "10" (#847/#848 P18).
      const line = {
        lineStyle: { color: mark.color ?? "#888", type: "dashed" },
        symbol: "none",
        label: { show: true, position: "insideEndTop" },
      };
      let data: unknown[];
      // Positions go through the axis: a category axis reads a number as an
      // INDEX, so a rule at the category 2022 must be sent as its index.
      // A datum or value with no position is left out — never sent as null,
      // on which ECharts throws and the whole chart breaks — and a note says so.
      const datumLine = (c: "x" | "y", axis: Axis | null, datum: Scalar, title?: string) => {
        const at = axis ? axis.pos(datum) : null;
        if (at === null) {
          notes.push(`rule at ${JSON.stringify(datum)} is off the ${c} axis — not drawn`);
          return [];
        }
        return [{ [`${c}Axis`]: at, name: title }];
      };
      // A rule's own values — a single field's rows, a segment's four ends —
      // are placed one way: through the axis's `pos`, with a number sent as
      // text (a layer that typed the field otherwise) read as ECharts reads
      // it. What has no place is left out and counted in a note.
      const place = (axis: Axis | null, c: Channel | undefined, r: number): number | null => {
        if (!axis || !c?.field) return null;
        const v = cols[c.field].value(r);
        const onNumbers =
          axis.kind === "value" || axis.kind === "log" || (axis.kind === "index" && axis.channel.type === "quantitative");
        const numeric = onNumbers && typeof v === "string" && v.trim() !== "";
        return axis.pos(numeric ? Number(v) : v);
      };
      const placed = (key: "xAxis" | "yAxis", axis: Axis | null, c: Channel | undefined) => {
        const lines = all.flatMap((r) => {
          const v = place(axis, c, r);
          return v === null ? [] : [{ [key]: v }];
        });
        const off = all.length - lines.length;
        if (off > 0) notes.push(`${off} rule ${off === 1 ? "value" : "values"} with no place on the ${key[0]} axis — not drawn`);
        return lines;
      };
      if (enc.y?.datum !== undefined) data = datumLine("y", yAxis, enc.y.datum, enc.y.title);
      else if (enc.x?.datum !== undefined) data = datumLine("x", xAxis, enc.x.datum, enc.x.title);
      else if (enc.y?.field && !enc.x?.field) data = placed("yAxis", yAxis, enc.y);
      else if (enc.x?.field && !enc.y?.field) data = placed("xAxis", xAxis, enc.x);
      else {
        // A segment needs both ends placed (an x2 / y2 with no place used to
        // fall back to x / y); a row with one that is not is left out.
        data = all.flatMap((r) => {
          const x = place(xAxis, enc.x, r);
          const y = place(yAxis, enc.y, r);
          const x2 = enc.x2?.field ? place(xAxis, enc.x2, r) : x;
          const y2 = enc.y2?.field ? place(yAxis, enc.y2, r) : y;
          return [x, y, x2, y2].some((v) => v === null) ? [] : [[{ coord: [x, y] }, { coord: [x2, y2] }]];
        });
        const off = all.length - data.length;
        if (off > 0) notes.push(`${off} rule ${off === 1 ? "segment" : "segments"} with no place on the axes — not drawn`);
      }
      push({ type: "line", data: [], markLine: { ...line, data } }, []);
      return;
    }

    if (mark.type === "pie") {
      const theta = cols[enc.theta?.field as string];
      const colour = cols[enc.color?.field as string];
      const data = all.map((r) => ({
        name: colour ? String(colour.value(r) ?? NONE) : String(r),
        value: theta.value(r),
        ...(lit && !lit[r] ? { itemStyle: { opacity: DIM_OPACITY } } : {}),
      }));
      legend.push(...data.map((d) => d.name));
      push({ type: "pie", data, radius: ["0%", "70%"], ...common(mark) }, all);
      return;
    }

    if (mark.type === "heatmap") {
      const c = cols[enc.color?.field as string];
      const [lo, hi] = range(c);
      const scheme = enc.color?.scale?.scheme ?? "sequential";
      const reach = Math.max(Math.abs(lo), Math.abs(hi));
      const [vmin, vmax] = scheme === "diverging" ? [-reach, reach] : [lo, hi];
      visualMaps.push({
        type: "continuous",
        min: vmin,
        max: vmax,
        dimension: 2,
        seriesIndex: series.length,
        calculable: true,
        ...scaleLabels(vmin, vmax, true),
        inRange: { color: palette(scheme, vmin, vmax) },
      });
      push({ type: "heatmap", data: all.map((r) => item(point(li, r, [c.value(r) as number | null]), r)), ...common(mark) }, all);
      return;
    }

    if (mark.type === "boxplot") {
      const five = ["$lo", "$q1", "$mid", "$q3", "$hi"].map((k) => cols[k]);
      const data = all.map((r) => item([xAt(li, r), ...five.map((c) => c.value(r) as number)], r));
      push({ type: "boxplot", data, encode: { x: 0, y: [1, 2, 3, 4, 5] }, ...common(mark) }, all);
      if (wire.outliers) {
        const out = Object.fromEntries(Object.entries(wire.outliers.columns).map(([k, w]) => [k, decodeColumn(w)]));
        const data2 = Array.from({ length: wire.outliers.rows }, (_, r) => [
          xAxis && enc.x?.field ? xAxis.at(out[enc.x.field], r) : null,
          out[enc.y?.field as string].value(r) as number,
        ]);
        push({ type: "scatter", data: data2, symbolSize: 5, ...common(mark) }, []);
      }
      return;
    }

    if (mark.type === "errorbar") {
      const [lo, hi] = enc.y2?.field ? [cols[enc.y!.field!], cols[enc.y2.field]] : [cols.$lo, cols.$hi];
      const data = all.map((r) => [xAt(li, r), lo.value(r) as number, hi.value(r) as number]);
      push(
        {
          type: "custom",
          data,
          encode: { x: 0, y: [1, 2] },
          renderItem: (
            params: { dataIndex: number },
            api: { value: (d: number) => number; coord: (p: number[]) => number[]; style: () => unknown },
          ) => {
            const x = api.value(0);
            const a = api.coord([x, api.value(1)]);
            const b = api.coord([x, api.value(2)]);
            const cap = 4;
            const opacity = lit && !lit[params.dataIndex] ? DIM_OPACITY : 1;
            const style = { stroke: mark.color ?? "#555", lineWidth: 1.5, opacity };
            return {
              type: "group",
              children: [
                { type: "line", shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, style },
                { type: "line", shape: { x1: a[0] - cap, y1: a[1], x2: a[0] + cap, y2: a[1] }, style },
                { type: "line", shape: { x1: b[0] - cap, y1: b[1], x2: b[0] + cap, y2: b[1] }, style },
              ],
            };
          },
          ...common(mark),
        },
        all,
      );
      return;
    }

    // line / area / bar / scatter / text: split by a categorical colour.
    const extras: Column[] = [];
    let sizeDim = -1;
    const colourCh = enc.color;
    if (colourCh?.field && colourCh.type === "quantitative") {
      const c = cols[colourCh.field];
      const [lo, hi] = range(c);
      const scheme = colourCh.scale?.scheme ?? "sequential";
      visualMaps.push({
        type: "continuous",
        min: lo,
        max: hi,
        dimension: 2,
        seriesIndex: series.length,
        calculable: true,
        ...scaleLabels(lo, hi, true),
        inRange: { color: palette(scheme, lo, hi) },
      });
      extras.push(c);
    }
    const sizeCol = wire.binned ? cols.$count : enc.size?.field ? cols[enc.size.field] : undefined;
    if (sizeCol) {
      sizeDim = 2 + extras.length;
      extras.push(sizeCol);
    }
    if (wire.binned) notes.push(`${wire.binned.points.toLocaleString("en-US")} points drawn as ${wire.binned.bins.toLocaleString("en-US")} bins`);
    const [smin, smax] = sizeCol ? range(sizeCol) : [0, 0];
    const symbolSize =
      sizeDim >= 0
        ? (v: number[]) => (smax === smin ? 10 : 4 + ((v[sizeDim] - smin) / (smax - smin)) * 20)
        : mark.type === "text"
          ? 0
          : undefined;

    const groups = new Map<string, number[]>();
    const splitCol = colourCh?.field && colourCh.type !== "quantitative" ? cols[colourCh.field] : undefined;
    for (const r of all) {
      const key = splitCol ? String(splitCol.value(r) ?? NONE) : "";
      const list = groups.get(key) ?? [];
      list.push(r);
      groups.set(key, list);
    }
    const order = splitCol
      ? [
          ...categories(colourCh!.field!, colourCh!, [cols]).map(String).filter((k) => groups.has(k)),
          ...(groups.has(NONE) ? [NONE] : []),
        ]
      : [""];
    const textCol = enc.text?.field ? cols[enc.text.field] : undefined;
    for (const key of order) {
      const members = groups.get(key) ?? [];
      const type = mark.type === "area" ? "line" : mark.type === "text" ? "scatter" : mark.type;
      const s: Record<string, unknown> = {
        type,
        data: members.map((r) => item(point(li, r, extras.map((c) => c.value(r) as number | null)), r)),
        ...common(mark),
      };
      if (splitCol) {
        s.name = key;
        legend.push(key);
      }
      if (symbolSize !== undefined) s.symbolSize = symbolSize;
      if (mark.type === "area") s.areaStyle = { opacity: mark.opacity ?? 0.7 };
      else if (mark.opacity !== undefined) s.itemStyle = { ...(s.itemStyle as object), opacity: mark.opacity };
      // A line is its stroke: itemStyle alone faded only the (hidden) points.
      if (mark.type === "line" && mark.opacity !== undefined) s.lineStyle = { opacity: mark.opacity };
      if (mark.type === "line" || mark.type === "area") {
        // A highlight dims POINTS, so a highlighted line shows them.
        s.showSymbol = mark.point ?? lit !== null;
        if (mark.smooth) s.smooth = true;
      }
      if ((mark.type === "area" || mark.type === "bar") && mark.stack) s.stack = "stack";
      // Large mode drops per-point styles, so a highlighted layer keeps it off.
      if (mark.type === "scatter" && n > 2000 && !lit) s.large = true;
      if (textCol) {
        s.label = { show: true, formatter: (p: { dataIndex: number }) => show(textCol, members[p.dataIndex]) };
      }
      push(s, members);
    }
  });

  const tooltip = {
    trigger: "item",
    confine: true,
    formatter: (p: { seriesIndex: number; dataIndex: number }) => {
      const where = rows[p.seriesIndex];
      if (!where) return "";
      const row = where.rows[p.dataIndex];
      if (row === undefined) return "";
      const enc = specs[where.layer].encoding;
      const cols = decoded[where.layer];
      const lines = tooltipChannels(enc)
        .filter((c) => cols[c.field!])
        .map((c) => `${escape(c.title ?? c.field!)}: <b>${escape(show(cols[c.field!], row))}</b>`);
      if (answer.layers[where.layer].binned && cols.$count) lines.push(`points: <b>${escape(show(cols.$count, row))}</b>`);
      return lines.join("<br/>");
    },
  };

  const option: Record<string, unknown> = {
    animation: false,
    // #847/#848 P14: time axes read the column's clock (see clock.ts), never
    // the viewer's zone
    useUTC: true,
    // the category palette, set rather than left to ECharts' default so a
    // category grid's raster reads the same constant (#847/#848 P19)
    color: CATEGORY_COLOURS,
    tooltip,
    series,
    brush: { toolbox: ["rect", "polygon", "clear"], xAxisIndex: cartesian ? 0 : undefined, throttleType: "debounce", throttleDelay: 250 },
    // The top row is the toolbox's: the chart draws no title of its own (#847/#848
    // PR 5 P13) — the view header right above it shows the spec's `title:`, and
    // a second copy in the canvas cost a row of a narrow pane and sat under
    // these icons. A legend takes the row below.
    toolbox: { top: 4, right: 8, feature: { brush: { type: ["rect", "polygon", "clear"] } } },
  };
  if (cartesian) {
    const lengthIsValue = specs.some((s) => ["bar", "area"].includes(markOf(s).type));
    option.xAxis = [axisOption(xAxis, gridCells, "x", lengthIsValue)];
    option.yAxis = [axisOption(yAxis, gridCells, "y", lengthIsValue)];
    // containLabel reserves room for tick labels only; the axis names (centred
    // beside their axis, NAME_AT) need their own margin or they are clipped.
    // A category legend's names need their own room beside the plot: at the
    // colour bar's fixed 80 px a long name ran over the plot (#847/#848 P19).
    // Up to ~8 px a character at 12 px (a browser's "gamma" measured ~43 px),
    // plus the swatch, its gap and the edge.
    const names = visualMaps.flatMap((v) => (v.type === "piecewise" ? (v.categories as string[]) : []));
    const legendRoom = names.length ? 56 + 8 * Math.max(...names.map((n) => n.length)) : 0;
    option.grid = {
      containLabel: true,
      left: 48,
      right: visualMaps.length ? Math.max(80, legendRoom) : 16,
      top: legend.length ? 48 : 32,
      bottom: 32,
    };
  }
  if (legend.length) option.legend = { data: [...new Set(legend)], top: 24, type: "scroll" };
  if (visualMaps.length) option.visualMap = visualMaps.map((v) => ({ right: 8, top: "middle", ...v }));
  return { option, series: rows, names, slices, grids, notes };
}
