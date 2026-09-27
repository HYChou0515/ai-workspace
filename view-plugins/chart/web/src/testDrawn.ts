/**
 * Test helpers: what a real (SSR) ECharts instance DRAWS for a data item --
 * the fill, stroke and opacity of the element on screen -- and an oracle for
 * "the same colour, desaturated, its lightness kept" (#847/#848 PR 5 P45 row
 * 41), written from the HSL definition rather than from ECharts' own code.
 */
import type * as echarts from "echarts/core";

export type Drawn = { fill?: string; stroke?: string; opacity: number };

type El = {
  style?: { fill?: unknown; stroke?: unknown; opacity?: number };
  childAt?: (i: number) => El | undefined;
  childCount?: () => number;
  isGroup?: boolean;
  parent?: El;
  invisible?: boolean;
};

/** The first shape drawn for item `i` of series `s` (a symbol, a bar, a
 * box, an errorbar's stem), with its opacity times its groups'. */
export function drawn(chart: echarts.ECharts, s: number, i: number): Drawn {
  type Data = { getItemGraphicEl(i: number): El | undefined };
  const model = (chart as unknown as { getModel(): { getSeriesByIndex(i: number): { getData(): Data } } }).getModel();
  let el = model.getSeriesByIndex(s).getData().getItemGraphicEl(i);
  if (!el) throw new Error(`series ${s} draws no item ${i}`);
  while (el.isGroup) {
    const first: El | undefined = el.childAt?.(0);
    if (!first) throw new Error(`series ${s} item ${i} is an empty group`);
    el = first;
  }
  let opacity = el.style?.opacity ?? 1;
  for (let p = el.parent; p; p = p.parent) opacity *= p.style?.opacity ?? 1;
  const colour = (v: unknown) => (typeof v === "string" && v !== "none" ? v : undefined);
  return { fill: colour(el.style?.fill), stroke: colour(el.style?.stroke), opacity };
}

/** [r, g, b] 0..255 of "#rgb", "#rrggbb", "rgb(...)" or "rgba(...)". */
export function rgb(colour: string): [number, number, number] {
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(colour);
  if (hex) {
    const h = hex[1]!.length === 3 ? [...hex[1]!].map((c) => c + c).join("") : hex[1]!;
    return [0, 2, 4].map((k) => parseInt(h.slice(k, k + 2), 16)) as [number, number, number];
  }
  const fn = /^rgba?\(([^)]+)\)$/i.exec(colour.replace(/\s+/g, ""));
  if (fn) return fn[1]!.split(",").slice(0, 3).map(Number) as [number, number, number];
  throw new Error(`not a colour: ${colour}`);
}

/** Whether two colour strings are one colour ("#5470c6" and
 * "rgba(84,112,198,1)" are). */
export function sameColour(a: string, b: string): boolean {
  return rgb(a).join() === rgb(b).join();
}

/** HSL lightness, 0..1: the mean of the largest and smallest channel. */
export function lightness(colour: string): number {
  const c = rgb(colour);
  return (Math.max(...c) + Math.min(...c)) / 2 / 255;
}

/** Whether `colour` is `own` desaturated with its lightness kept: a grey
 * (every channel equal, give or take rounding) as light as `own`. */
export function desaturated(colour: string, own: string): boolean {
  const c = rgb(colour);
  return Math.max(...c) - Math.min(...c) <= 1 && Math.abs(lightness(colour) - lightness(own)) <= 1.5 / 255;
}
