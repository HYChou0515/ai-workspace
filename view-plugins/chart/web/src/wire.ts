/**
 * Decoding what the sandbox's `query` sends (`chart_view/wire.py` documents the
 * format; `wire-corpus/` pins both halves to it).
 */

export type WireColumn =
  | { kind: "f64"; data: string }
  | { kind: "time"; data: string }
  | { kind: "cat"; levels: (string | number | boolean)[]; width: 1 | 2 | 4; codes: string }
  | { kind: "q8"; min: number; max: number; codes: string };

export type Scalar = string | number | boolean;

/** A decoded column: its length, and row i as a value (null = missing). */
export type Column = {
  kind: WireColumn["kind"];
  length: number;
  value(i: number): Scalar | null;
  /** cat / q8: the raw code of row i (the missing code for a missing row). */
  code?(i: number): number;
  levels?: Scalar[];
  min?: number;
  max?: number;
};

function bytes(b64: string): DataView {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return new DataView(out.buffer);
}

export function decodeColumn(w: WireColumn): Column {
  switch (w.kind) {
    case "f64":
    case "time": {
      const view = bytes(w.data);
      const length = view.byteLength / 8;
      return {
        kind: w.kind,
        length,
        value: (i) => {
          const v = view.getFloat64(i * 8, true);
          return Number.isNaN(v) ? null : v;
        },
      };
    }
    case "cat": {
      const view = bytes(w.codes);
      const missing = 2 ** (8 * w.width) - 1;
      const read =
        w.width === 1
          ? (i: number) => view.getUint8(i)
          : w.width === 2
            ? (i: number) => view.getUint16(i * 2, true)
            : (i: number) => view.getUint32(i * 4, true);
      return {
        kind: "cat",
        length: view.byteLength / w.width,
        levels: w.levels,
        code: read,
        value: (i) => {
          const c = read(i);
          return c === missing ? null : w.levels[c];
        },
      };
    }
    case "q8": {
      const view = bytes(w.codes);
      const span = w.max - w.min;
      return {
        kind: "q8",
        length: view.byteLength,
        min: w.min,
        max: w.max,
        code: (i) => view.getUint8(i),
        value: (i) => {
          const c = view.getUint8(i);
          return c === 255 ? null : w.min + (c / 254) * span;
        },
      };
    }
  }
}

/** Row i is bit i % 8 (LSB first) of byte i / 8. */
export function decodeBits(b64: string, rows: number): boolean[] {
  const view = bytes(b64);
  return Array.from({ length: rows }, (_, i) => ((view.getUint8(i >> 3) >> (i & 7)) & 1) === 1);
}

/** The opaque string a marking compares for `v` (Q6): what String(v) writes,
 * and nothing for a missing or infinite value — the sandbox's `canon`. */
export function canon(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "number" && !Number.isFinite(v)) return null;
  return String(v);
}
