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
 * the first's value under its own label. */
function oneOp(path: string, encoding: Encoding): string[] {
  const first = new Map<string, [string, string]>();
  const lines: string[] = [];
  for (const [channel, d] of fields(encoding)) {
    const op = d.aggregate;
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

/** Every way a schema-valid `doc` breaks these rules, one line each. */
export function ruleErrors(doc: unknown): string[] {
  const lines: string[] = [];
  for (const [path, layer] of layers(doc as Doc)) lines.push(...oneOp(path, layer.encoding ?? {}));
  return lines;
}
