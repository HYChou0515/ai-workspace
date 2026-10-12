/**
 * The model settings page's patch, as the page reads it (`docs/plan-model-settings.md`).
 *
 * A patch is a JSON Merge Patch over the deploy defaults: an object merges into
 * the default, anything else — a value, a list, `null` (hides a preset) —
 * replaces it. So a patch's "values" are its non-object leaves, and every
 * question the page asks of it is a question about those leaves by path:
 * what this page changed (flatten), what Apply would change (diff), and how to
 * give one value back to its default (remove it).
 */

export type Patch = Record<string, unknown>;

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Every value the patch sets, `[dotted path, value]`, in its own order. */
export function flattenPatch(patch: Patch, prefix = ""): [string, unknown][] {
  return Object.entries(patch).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return isObject(value) ? flattenPatch(value, path) : [[path, value] as [string, unknown]];
  });
}

export type Change =
  | { path: string; kind: "added"; after: unknown }
  | { path: string; kind: "removed"; before: unknown }
  | { path: string; kind: "changed"; before: unknown; after: unknown };

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** What publishing `after` in place of `before` changes, value by value. */
export function diffPatches(before: Patch, after: Patch): Change[] {
  const was = new Map(flattenPatch(before));
  const will = new Map(flattenPatch(after));
  const out: Change[] = [];
  for (const [path, value] of was) {
    if (!will.has(path)) out.push({ path, kind: "removed", before: value });
    else if (!same(value, will.get(path)))
      out.push({ path, kind: "changed", before: value, after: will.get(path) });
  }
  for (const [path, value] of will) if (!was.has(path)) out.push({ path, kind: "added", after: value });
  return out;
}

/** `patch` without the value at `path` — that value back to its default — on a copy. */
export function withoutPath(patch: Patch, path: string): Patch {
  const [head, ...rest] = path.split(".");
  if (!(head in patch)) return patch;
  const out = { ...patch };
  const child = out[head];
  if (rest.length === 0 || !isObject(child)) {
    delete out[head];
    return out;
  }
  const trimmed = withoutPath(child, rest.join("."));
  if (Object.keys(trimmed).length === 0) delete out[head];
  else out[head] = trimmed;
  return out;
}

export type Parsed = { ok: true; patch: Patch } | { ok: false; error: string };

/** The editor's text as a patch; `error` is the parser's own words, or `not-an-object`. */
export function parsePatch(text: string): Parsed {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
  return isObject(value) ? { ok: true, patch: value } : { ok: false, error: "not-an-object" };
}
