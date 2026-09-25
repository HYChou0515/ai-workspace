/**
 * Test helpers: build a query answer the way the sandbox encodes one (the
 * format itself is pinned by wire-corpus/ against the real encoder).
 */
import type { Answer, WireLayer } from "./option";
import type { WireColumn } from "./wire";

function b64(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

export function f64(values: (number | null)[]): WireColumn {
  const buf = new DataView(new ArrayBuffer(values.length * 8));
  values.forEach((v, i) => buf.setFloat64(i * 8, v ?? Number.NaN, true));
  return { kind: "f64", data: b64(new Uint8Array(buf.buffer)) };
}

export function time(values: string[]): WireColumn {
  const col = f64(values.map((v) => Date.parse(v)));
  return { kind: "time", data: (col as { data: string }).data };
}

export function cat(values: (string | number | null)[]): WireColumn {
  const levels = [...new Set(values.filter((v): v is string | number => v !== null))].sort();
  const codes = new Uint8Array(values.map((v) => (v === null ? 255 : levels.indexOf(v))));
  return { kind: "cat", levels, width: 1, codes: b64(codes) };
}

export function q8(codes: number[], min: number, max: number): WireColumn {
  return { kind: "q8", min, max, codes: b64(new Uint8Array(codes)) };
}

export function wideCodes(width: 2 | 4, values: number[]): string {
  const view = new DataView(new ArrayBuffer(values.length * width));
  values.forEach((v, i) => (width === 2 ? view.setUint16(i * 2, v, true) : view.setUint32(i * 4, v, true)));
  return b64(new Uint8Array(view.buffer));
}

export function layer(
  mark: string,
  rows: number,
  columns: Record<string, WireColumn>,
  extra: Partial<WireLayer> = {},
): WireLayer {
  return { mark, rows, columns, highlight: null, lit: null, binned: null, outliers: null, measured: [], ...extra };
}

export function answer(...layers: WireLayer[]): Answer {
  return { format: 1, layers };
}

export const base = { view: "chart", source: "data/a.csv" };
