/**
 * The rules the schema cannot state: each compares one part of a chart spec
 * with another, which JSON Schema has no way to say.
 *
 * The sandbox holds the same rules in `chart_view/rules.py`, word for word;
 * the spec corpus (`spec-corpus/`) and the message tests on both sides hold
 * the two to one verdict. Run only over a document the schema already
 * accepted, so the shapes here are trusted.
 */

type Def = { field?: string; type?: string; aggregate?: string };
type Encoding = Record<string, Def | Def[] | undefined>;
type Layer = { mark?: unknown; encoding?: Encoding };
type Doc = Layer & { layer?: Layer[]; keys?: string[]; highlight?: { where?: string; values?: Record<string, unknown> } };

const CHANNELS = ["x", "y", "x2", "y2", "color", "size", "theta", "text"];

const NUMBER = /0[xXoObB][0-9a-fA-F_]+|\d[\d_]*(?:\.[\d_]*)?(?:[eE][+-]?\d[\d_]*)?[jJ]?/y;
const NAME = /[\p{L}\p{Nl}_][\p{L}\p{Nl}\p{Mn}\p{Mc}\p{Nd}\p{Pc}]*/uy;
// Python's words, and pandas' own name for the row index (read as a column
// only when a column has that name; a field is never needed for it)
const NOT_COLUMNS = new Set(["and", "or", "not", "in", "is", "True", "False", "None", "index"]);
const TEXT_PREFIX = /^[rRbBuUfF]{1,2}$/;
const DIGIT = /\d/;

/** The columns a `where:` expression reads, as pandas reads them: each name
 * that is not a Python word, an attribute (`.isin`), a function called
 * (`abs(`), a local (`@limit`) or the prefix of a text (`r'...'`); inside
 * backticks, the text between them. Texts in quotes are skipped. Held to
 * pandas itself by `wire-corpus/where-names.json`. */
export function whereNames(expr: string): string[] {
  const names: string[] = [];
  let i = 0;
  const n = expr.length;
  while (i < n) {
    const ch = expr[i]!;
    if (ch === "'" || ch === '"') {
      i += 1;
      while (i < n && expr[i] !== ch) i += expr[i] === "\\" ? 2 : 1;
      i += 1;
    } else if (ch === "`") {
      const end = expr.indexOf("`", i + 1);
      if (end < 0) break;
      names.push(expr.slice(i + 1, end));
      i = end + 1;
    } else if (DIGIT.test(ch)) {
      // (a leading dot is skipped, then its digits read)
      NUMBER.lastIndex = i;
      NUMBER.exec(expr);
      i = NUMBER.lastIndex;
    } else {
      NAME.lastIndex = i;
      const name = NAME.exec(expr);
      if (!name) {
        i += 1;
        continue;
      }
      const word = name[0];
      const start = i;
      i = NAME.lastIndex;
      const before = expr.slice(0, start).trimEnd().slice(-1);
      const after = expr.slice(i).trimStart().slice(0, 1);
      const quote = expr[i] === "'" || expr[i] === '"';
      if (NOT_COLUMNS.has(word) || before === "." || before === "@" || after === "(" || (quote && TEXT_PREFIX.test(word))) continue;
      names.push(word);
    }
  }
  return [...new Set(names)];
}

/** [the path its lines start with, the layer] for each layer of `doc`. */
function layers(doc: Doc): [string, Layer][] {
  return doc.layer ? doc.layer.map((ly, i) => [`layer[${i}].`, ly]) : [["", doc]];
}

/** [channel, definition] for each channel naming a field, in the order the
 * sandbox's query reads them -- a tooltip list's items as `tooltip[i]`. */
function fields(encoding: Encoding): [string, Def][] {
  const out: [string, Def][] = [];
  for (const c of CHANNELS) {
    const d = encoding[c] as Def | undefined;
    if (d && "field" in d) out.push([c, d]);
  }
  const tips = encoding.tooltip;
  if (Array.isArray(tips)) tips.forEach((t, i) => out.push([`tooltip[${i}]`, t]));
  else if (tips) out.push(["tooltip", tips]);
  return out;
}

/** #847/#848 PR 5 P41 row 26: a layer aggregates a field once (the first
 * channel naming it decides), so a second op on the same field would show
 * the first's value under its own label. A stack's value channel carries the
 * op its segments are (P42 row 31): its own `aggregate`, else sum. */
function oneOp(path: string, layer: Layer): string[] {
  const stack = stackParts(layer);
  const first = new Map<string, [string, string]>();
  const lines: string[] = [];
  for (const [channel, d] of fields(layer.encoding ?? {})) {
    const op = stack && channel === stack.channel ? stack.op : d.aggregate;
    if (!op) continue;
    const field = d.field as string;
    const seen = first.get(field);
    if (!seen) {
      first.set(field, [channel, op]);
      continue;
    }
    const [on, was] = seen;
    if (op !== was) {
      lines.push(
        `${path}encoding.${channel}: '${field}' is aggregated as ${was} on ${on} — a field ` +
          `has one aggregate in a layer, so ${op} here would show the ${was}: use ${was} ` +
          "here too, or compute both in a transform aggregate, each under its own name (as:)",
      );
    }
  }
  return lines;
}

/** A stacked layer's parts: its slot and colour fields (what it links by),
 * its value field, the channel naming it (x or y) and what each segment is of
 * its rows (the value's `aggregate`, else sum). */
export type StackParts = { links: string[]; value: string; channel: string; op: string };

/** A stacked layer's parts; null for a layer that is not a stack -- a bar or
 * an area with `stack: true`. The slot is y when y is a category (a
 * horizontal bar), else x; a colour by value is refused by the schema. */
export function stackParts(layer: Layer): StackParts | null {
  const mark = layer.mark as { type?: string; stack?: unknown } | string | undefined;
  if (typeof mark !== "object" || !(mark.type === "bar" || mark.type === "area") || mark.stack !== true) return null;
  const encoding = layer.encoding as Record<string, Def>;
  const horizontal = encoding.y!.type === "nominal" || encoding.y!.type === "ordinal";
  const [slot, value] = horizontal ? ["y", "x"] : ["x", "y"];
  const colour = encoding.color?.field;
  const links = [encoding[slot]!.field as string, ...(colour !== undefined ? [colour] : [])];
  const op = encoding[value]!.aggregate ?? "sum";
  return { links: [...new Set(links)], value: encoding[value]!.field as string, channel: value, op };
}

const quoted = (names: string[]) => names.map((n) => `'${n}'`).join(", ");

/** #847/#848 PR 5 P41 row 21 [user, 2026-09-26]: a stack links by its slot
 * and colour only. A segment is the sum of its rows (P40 row 18) -- or the
 * value's own aggregate of them (the words name which: P42 row 33) -- so it
 * has no single value of any other field: a `keys:` naming one wrote nothing
 * a linked view could light, and a `highlight:` reading one lit nothing. A
 * highlight may also test the value, which is each segment's sum (or
 * mean...). */
function stackLinks(doc: Doc, path: string, layer: Layer): string[] {
  const parts = stackParts(layer);
  if (!parts) return [];
  const { links, value, op } = parts;
  const subject = path ? `a stack (${path.replace(/\.$/, "")})` : "a stack";
  const head =
    `${subject} links by its slot and colour only (${quoted(links)}) — a segment is the` +
    ` ${op} of its rows, so it has no single`;
  const lines: string[] = [];
  const keys = (doc.keys ?? []).filter((k) => !links.includes(k));
  if (keys.length > 0) {
    lines.push(`keys: ${head} ${quoted(keys)}: key the view by its slot and colour, or drop stack so single rows link`);
  }
  const highlight = doc.highlight ?? {};
  const reads: ["where" | "values", () => string[]][] = [
    ["where", () => whereNames(highlight.where as string)],
    ["values", () => Object.keys(highlight.values as object)],
  ];
  for (const [how, read] of reads) {
    const other = how in highlight ? read().filter((c) => !links.includes(c) && c !== value) : [];
    if (other.length > 0) {
      lines.push(
        `highlight.${how}: ${head} ${quoted(other)}: test its slot and colour, or its` +
          ` value '${value}' (each segment's ${op}), or drop stack so single rows light`,
      );
    }
  }
  return lines;
}

/** Every way a schema-valid `doc` breaks these rules, one line each. */
export function ruleErrors(doc: unknown): string[] {
  const lines: string[] = [];
  for (const [path, layer] of layers(doc as Doc)) {
    lines.push(...oneOp(path, layer));
    lines.push(...stackLinks(doc as Doc, path, layer));
  }
  return lines;
}
