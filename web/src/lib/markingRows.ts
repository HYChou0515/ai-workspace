/**
 * A table's rows as marking text (#847/#848 PR 5 P1).
 *
 * A marking holds the strings a chart writes: `canon` over what the chart's
 * sandbox sends for a `keys:` column (pandas' reading of the source, printed
 * as JavaScript's `String` would). A table never visits the sandbox, so it
 * has to arrive at the same strings from what IT holds — the file's text for a
 * CSV/TSV table, the records as the API sends them for an entity table — or a
 * table and a chart on one marking disagree about which rows are marked.
 *
 * The oracle is `view-plugins/chart/wire-corpus/table-rows.json`, written from
 * the chart's own answer; `web/tests/markingRows.corpus.test.ts` holds this
 * module to it. What the browser cannot recover (and so reads differently from
 * a chart): a CSV float written with 17 significant digits, which pandas'
 * parser rounds to a neighbouring double; a whole float or an integer past
 * 2^53 inside a list, and a mapping with a non-text key (JSON erases both);
 * text shaped exactly like an ISO date-time, which JSON sends as it sends a
 * date-time.
 *
 * Whether a row is lit is `isLit` — the platform's one rule — over these rows.
 */
import { isLit, type Marking } from "./markings";

/** A table row as a marking compares it: column → marking text, a missing
 * value left out (as a chart leaves out a row's missing key). */
export type MarkingRow = Readonly<Record<string, string>>;

// ── entity records (JSON, as the API sends them) ───────────────────────────

/** A date-time as pydantic sends it; the chart prints the same instant as
 * pandas / Python's `str` does: a space for the `T`, `+00:00` for `Z`. */
const JSON_DATETIME = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2}(?:\.\d{6})?)(Z|[+-]\d{2}:\d{2})?$/;

/** The marking text a chart writes for an entity field's JSON value, or null
 * for none (missing, not finite). */
export function markingText(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : null;
  if (typeof value === "string") {
    const m = JSON_DATETIME.exec(value);
    if (!m) return value;
    return `${m[1]} ${m[2]}${m[3] === "Z" ? "+00:00" : (m[3] ?? "")}`;
  }
  // A list or a mapping: the chart writes Python's `str` of it, every date in
  // it as ISO text (`wire._plain`).
  return pyRepr(value);
}

/** An entity record as a marking compares it: its `number` and each field. */
export function entityMarkingRow(record: {
  readonly number: number;
  readonly fields: Readonly<Record<string, unknown>>;
}): MarkingRow {
  const row: Record<string, string> = { number: String(record.number) };
  for (const [column, value] of Object.entries(record.fields)) {
    const text = markingText(value);
    if (text !== null) row[column] = text;
  }
  return row;
}

const LIST_DATETIME_UTC = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?)Z$/;

function pyRepr(value: unknown): string {
  if (value === null || value === undefined) return "None";
  if (typeof value === "boolean") return value ? "True" : "False";
  if (typeof value === "number") return pyNumber(value);
  if (typeof value === "string") {
    // `Timestamp.isoformat()` writes UTC as +00:00 where JSON writes Z.
    const m = LIST_DATETIME_UTC.exec(value);
    return pyStr(m ? `${m[1]}+00:00` : value);
  }
  if (Array.isArray(value)) return `[${value.map(pyRepr).join(", ")}]`;
  const entries = Object.entries(value as Record<string, unknown>).map(([k, v]) => [pyStr(k), v] as const);
  entries.sort(([a], [b]) => byCodePoint(a, b));
  return `{${entries.map(([k, v]) => `${k}: ${pyRepr(v)}`).join(", ")}}`;
}

/** Python's `repr` of a number JSON carried: an integer when it is one a
 * double holds exactly, else a float. */
function pyNumber(x: number): string {
  if (Number.isNaN(x)) return "nan";
  if (!Number.isFinite(x)) return x > 0 ? "inf" : "-inf";
  if (Object.is(x, -0)) return "-0.0";
  if (Number.isSafeInteger(x)) return String(x);
  // The shortest round-trip digits (JavaScript's and Python's agree), laid
  // out as Python's float repr: fixed while -4 < decpt <= 16, else 1e+16.
  const [mantissa, exp] = Math.abs(x).toExponential().split("e") as [string, string];
  const digits = mantissa.replace(".", "");
  const e = Number(exp);
  const decpt = e + 1;
  const sign = x < 0 ? "-" : "";
  if (decpt > -4 && decpt <= 16) {
    if (decpt <= 0) return `${sign}0.${"0".repeat(-decpt)}${digits}`;
    if (decpt >= digits.length) return `${sign}${digits}${"0".repeat(decpt - digits.length)}.0`;
    return `${sign}${digits.slice(0, decpt)}.${digits.slice(decpt)}`;
  }
  const head = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
  return `${sign}${head}e${e < 0 ? "-" : "+"}${String(Math.abs(e)).padStart(2, "0")}`;
}

/** What Python's `str.isprintable` refuses: other (control, format,
 * surrogate, private, unassigned) and separators other than the space. */
const UNPRINTABLE = /[\p{C}\p{Z}]/u;

/** Python's `repr` of a str. */
function pyStr(s: string): string {
  const quote = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = quote;
  for (const ch of s) {
    if (ch === "\\" || ch === quote) out += `\\${ch}`;
    else if (ch === "\t") out += "\\t";
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch !== " " && UNPRINTABLE.test(ch)) {
      const cp = ch.codePointAt(0)!;
      const hex = cp.toString(16);
      out += cp < 0x100 ? `\\x${hex.padStart(2, "0")}` : cp < 0x10000 ? `\\u${hex.padStart(4, "0")}` : `\\U${hex.padStart(8, "0")}`;
    } else out += ch;
  }
  return out + quote;
}

function byCodePoint(a: string, b: string): number {
  const x = [...a];
  const y = [...b];
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i]!.codePointAt(0)! - y[i]!.codePointAt(0)!;
    if (d !== 0) return d;
  }
  return x.length - y.length;
}

// ── CSV / TSV (the file's text, as pandas' read_csv types it) ──────────────

/** pandas' default missing-value tokens (`read_csv`'s `na_values`). */
const NA = new Set([
  "", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan", "1.#IND", "1.#QNAN",
  "<NA>", "N/A", "NA", "NULL", "NaN", "None", "n/a", "nan", "null",
]);
const BOOL = /^(?:true|false)$/i;
const INT = /^[ \t]*[+-]?\d+[ \t]*$/;
const FLOAT = /^[ \t]*[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?[ \t]*$/;
const INF = /^[+-]?inf(?:inity)?$/i;
const INT64 = [-(2n ** 63n), 2n ** 63n - 1n] as const;
const UINT64 = [0n, 2n ** 64n - 1n] as const;

function within(values: string[], [lo, hi]: readonly [bigint, bigint]): boolean {
  return values.every((v) => {
    const n = BigInt(v.trim());
    return n >= lo && n <= hi;
  });
}

/** A column's cells as marking text, typed as pandas types the column: all
 * booleans, all numbers, or text as written. */
function columnTexts(cells: readonly (string | undefined)[]): (string | null)[] {
  const present = cells.map((c) => (c === undefined || NA.has(c) ? null : c));
  const values = present.filter((c): c is string => c !== null);
  if (values.length === 0) return present;
  if (values.every((v) => BOOL.test(v))) return present.map((v) => v && v.toLowerCase());
  const number = (v: string | null) => (v === null || INF.test(v) ? null : String(Number(v.trim())));
  if (values.every((v) => INT.test(v))) {
    // Past int64 (and uint64 for a column with no negative), pandas keeps text.
    return within(values, INT64) || within(values, UINT64) ? present.map(number) : present;
  }
  if (values.every((v) => INF.test(v) || FLOAT.test(v))) {
    // A literal past the largest double is text to pandas, not infinity.
    const overflows = values.some((v) => !INF.test(v) && !Number.isFinite(Number(v.trim())));
    return overflows ? present : present.map(number);
  }
  return present;
}

/** A parsed CSV/TSV (row 0 the header, as `parseCsv` gives it) as marking
 * rows, one per body row. */
export function csvMarkingRows(rows: readonly (readonly string[])[]): MarkingRow[] {
  const [header = [], ...body] = rows;
  const texts = header.map((_, ci) => columnTexts(body.map((r) => r[ci])));
  return body.map((_, ri) => {
    const row: Record<string, string> = {};
    header.forEach((column, ci) => {
      const v = texts[ci]![ri];
      if (v !== null && v !== undefined) row[column] = v;
    });
    return row;
  });
}

// ── lighting ───────────────────────────────────────────────────────────────

/** Which rows `marking` lights — `isLit`, the platform's rule, row by row. */
export function litRows(rows: readonly MarkingRow[], marking: Marking): boolean[] {
  return rows.map((row) => isLit(row, marking));
}
