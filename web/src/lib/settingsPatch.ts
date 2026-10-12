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

export type Leaf = { path: string; parts: string[]; value: unknown };

/** Every value the patch sets, in its own order. `parts` is the path to act
 * on; `path` (joined with ".") is only for showing it — a preset name may hold
 * a dot (`qwen2.5`), so a joined path cannot be split back. */
export function flattenParts(patch: Patch, prefix: string[] = []): Leaf[] {
  return Object.entries(patch).flatMap(([key, value]) => {
    const parts = [...prefix, key];
    return isObject(value) ? flattenParts(value, parts) : [{ path: parts.join("."), parts, value }];
  });
}

/** Every value the patch sets, `[dotted path, value]`, in its own order. */
export function flattenPatch(patch: Patch): [string, unknown][] {
  return flattenParts(patch).map((leaf) => [leaf.path, leaf.value]);
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

/** The value at `parts` in `document`; `found` false when any part is absent. */
export function valueAt(document: unknown, parts: string[]): { found: boolean; value?: unknown } {
  let node: unknown = document;
  for (const part of parts) {
    if (!isObject(node) || !(part in node)) return { found: false };
    node = node[part];
  }
  return { found: true, value: node };
}

/** What changes in what RUNS when `after` is published in place of `before`,
 * both patches over `defaults` — value by value, for every path either patch
 * sets: a replaced default is "changed" from the default, a value given back
 * is "changed" to it, a `null` (a hidden preset) is "removed". */
export function diffEffective(defaults: Patch, before: Patch, after: Patch): Change[] {
  const paths = new Map<string, string[]>();
  for (const leaf of [...flattenParts(before), ...flattenParts(after)]) paths.set(leaf.path, leaf.parts);
  // What runs at `parts` under `patch`: a null at the path or ANY ancestor
  // hides it (a hidden preset — review r3: an ancestor fell through to the
  // defaults); a path the patch does not reach is the default.
  const runs = (patch: Patch, parts: string[]): { found: boolean; value?: unknown } => {
    let node: unknown = patch;
    for (const part of parts) {
      if (!isObject(node) || !(part in node)) return valueAt(defaults, parts);
      node = node[part];
      if (node === null) return { found: false };
    }
    return { found: true, value: node };
  };
  // Value by value: a path with a deeper path beside it is said through those.
  const leaves = [...paths].filter(([path]) => ![...paths.keys()].some((other) => other.startsWith(`${path}.`)));
  const out: Change[] = [];
  for (const [path, parts] of leaves) {
    const was = runs(before, parts);
    const will = runs(after, parts);
    if (was.found && will.found) {
      if (!same(was.value, will.value)) out.push({ path, kind: "changed", before: was.value, after: will.value });
    } else if (will.found) out.push({ path, kind: "added", after: will.value });
    else if (was.found) out.push({ path, kind: "removed", before: was.value });
  }
  return out;
}

/** `patch` without the value at `parts` — that value back to its default — on a copy. */
export function withoutPath(patch: Patch, parts: string[]): Patch {
  const [head, ...rest] = parts;
  if (head === undefined || !(head in patch)) return patch;
  const out = { ...patch };
  const child = out[head];
  if (rest.length === 0 || !isObject(child)) {
    delete out[head];
    return out;
  }
  const trimmed = withoutPath(child, rest);
  if (Object.keys(trimmed).length === 0) delete out[head];
  else out[head] = trimmed;
  return out;
}

type Frame = { kind: "obj" | "arr"; path: string | null };

/** The line each object key is written on, by dotted path — first occurrence;
 * keys inside arrays are not indexed (a patch replaces a list whole). */
function keyLines(text: string): Map<string, number> {
  const out = new Map<string, number>();
  const frames: Frame[] = [];
  let line = 1;
  let expectKey = false;
  let pendingKey: string | null = null;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    const top = frames[frames.length - 1];
    if (ch === "\n") line++;
    else if (ch === "{" || ch === "[") {
      const path = frames.length === 0 ? "" : top.kind === "arr" ? null : pendingKey;
      frames.push({ kind: ch === "{" ? "obj" : "arr", path: ch === "{" ? path : null });
      expectKey = ch === "{";
      pendingKey = null;
    } else if (ch === "}" || ch === "]") {
      frames.pop();
      expectKey = false;
    } else if (ch === ",") expectKey = top?.kind === "obj";
    else if (ch === ":") expectKey = false;
    else if (ch === '"') {
      let j = i + 1;
      while (j < text.length && text[j] !== '"') j += text[j] === "\\" ? 2 : 1;
      const raw = text.slice(i, j + 1);
      if (expectKey && top?.kind === "obj" && top.path !== null) {
        const key = JSON.parse(raw) as string;
        pendingKey = top.path ? `${top.path}.${key}` : key;
        if (!out.has(pendingKey)) out.set(pendingKey, line);
      } else pendingKey = null;
      expectKey = false;
      i = j;
    } else if (!/\s/.test(ch)) pendingKey = null;
  }
  return out;
}

/** The line `path` (dotted, as a problem names it) is written on in `text` —
 * or its deepest part the text has, since a problem can name a key the patch
 * leaves to the default. null when none of it is there, or the text is not JSON. */
export function lineOfPath(text: string, path: string): number | null {
  if (!parsePatch(text).ok) return null;
  const lines = keyLines(text);
  const parts = path.split(".");
  for (let n = parts.length; n > 0; n--) {
    const found = lines.get(parts.slice(0, n).join("."));
    if (found !== undefined) return found;
  }
  return null;
}

export type Parsed ={ ok: true; patch: Patch } | { ok: false; error: string };

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
