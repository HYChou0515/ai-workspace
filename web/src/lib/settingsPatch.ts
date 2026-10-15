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

import { CORE_SCHEMA, dump as dumpYaml, load as loadYaml } from "js-yaml";

import { sameShape } from "./sameShape";

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

// By shape, never by text (CLAUDE.md `sameShape`): a pasted list entry
// reorders its keys and is still the same entry (M23).
const same = sameShape;

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
  // A patch's object is merged over the defaults there (RFC 7396), as the
  // loader does: a preset shown again runs with its defaults under the patch's
  // values, and saying the fragment alone named only those (final review 6).
  const runs = (patch: Patch, parts: string[]): { found: boolean; value?: unknown } => {
    let node: unknown = patch;
    for (const part of parts) {
      if (!isObject(node) || !(part in node)) return valueAt(defaults, parts);
      node = node[part];
      if (node === null) return { found: false };
    }
    const under = valueAt(defaults, parts);
    return { found: true, value: mergePatch(under.found ? under.value : undefined, node) };
  };
  // What is said, decided on PARTS — never on joined paths: a preset name may
  // hold a dot, so `qwen2` is not above `qwen2.5` (confirmation round).
  // A null in either patch is one change — the preset stops (or starts)
  // running, all of it — said as itself; anything else, value by value.
  const all = [...paths.values()];
  const above = (a: string[], b: string[]) => a.length < b.length && a.every((part, i) => b[i] === part);
  const hidden = (patch: Patch, parts: string[]) => {
    const at = valueAt(patch, parts);
    return at.found && at.value === null;
  };
  const isHide = (parts: string[]) => hidden(before, parts) || hidden(after, parts);
  const leaves = [...paths].filter(([, parts]) => {
    if (all.some((other) => above(other, parts) && isHide(other))) return false;
    return isHide(parts) || !all.some((other) => above(parts, other));
  });
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

/** RFC 7396: `patch` over `target` — an object merges key by key, a `null`
 * removes its key, anything else replaces. */
function mergePatch(target: unknown, patch: unknown): unknown {
  if (!isObject(patch)) return patch;
  const out: Record<string, unknown> = isObject(target) ? { ...target } : {};
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) delete out[key];
    else out[key] = mergePatch(out[key], value);
  }
  return out;
}

/** What the editor shows (M23): the defaults with `patch` over them, as the
 * loader builds it — except that every `null` the patch holds is shown where it
 * was written, not merged away: left out, the text reads as "back to the
 * default", so a hidden preset lost its hide, and a null the check refuses
 * (review 17) vanished while the draft still held it. */
export function effectiveOf(defaults: Patch, patch: Patch): Patch {
  const show = (target: unknown, over: unknown): unknown => {
    if (!isObject(over)) return over;
    const out: Record<string, unknown> = isObject(target) ? { ...target } : {};
    for (const [key, value] of Object.entries(over)) out[key] = value === null ? null : show(out[key], value);
    return out;
  };
  return show(defaults, patch) as Patch;
}

/** A secret's mask, the page's (`*** set ***`) or the boot dump's
 * (`*** set (17 chars) via ${X} ***`): pasted back, it changes nothing. */
const MASK = /^\*\*\* (?:set|unset)(?: \(\d+ chars\))?(?: via \S+)? \*\*\*$/;
const isMask = (value: unknown) => typeof value === "string" && MASK.test(value);
const UNCHANGED = Symbol("unchanged");

/** Equal, a mask matching any mask — for a list, which is stored whole: an
 * entry's key masked by the page and by the boot dump is the same key (review 17). */
function alike(a: unknown, b: unknown): boolean {
  if (isMask(a) && isMask(b)) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => alike(x, b[i]));
  if (isObject(a) && isObject(b)) {
    const keys = Object.keys(a);
    return keys.length === Object.keys(b).length && keys.every((k) => k in b && alike(a[k], b[k]));
  }
  return same(a, b);
}

/** What is stored for an edited text (M23): only what differs from `base` —
 * the whole boot config and every App — so the person edits the settings as
 * they read and never writes a patch. Anything left out is the default (a
 * pasted fragment changes just itself); a preset is hidden only where `null`
 * is written for it (M12); an unchanged mask is no change; a section the page
 * does not edit drops out when unchanged and stays, for the check to refuse,
 * when changed. */
export function patchOf(base: Patch, edited: Patch): Patch {
  const diff = (was: unknown, now: unknown): unknown => {
    if (isObject(was) && isObject(now)) {
      const out: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(now)) {
        const d = diff(was[key], value);
        if (d !== UNCHANGED) out[key] = d;
      }
      return Object.keys(out).length ? out : UNCHANGED;
    }
    if (Array.isArray(was) && Array.isArray(now)) return alike(was, now) ? UNCHANGED : now;
    if (isMask(was) && isMask(now)) return UNCHANGED;
    return same(was, now) ? UNCHANGED : now;
  };
  const out = diff(base, edited);
  return out === UNCHANGED ? {} : (out as Patch);
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
function jsonKeyLines(text: string): Map<string, number> {
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

/** The line each mapping key is written on in block YAML, by dotted path —
 * first occurrence; keys inside a list are not indexed (a patch replaces a
 * list whole), as in `jsonKeyLines`. */
function yamlKeyLines(text: string): Map<string, number> {
  const out = new Map<string, number>();
  const frames: { indent: number; path: string | null }[] = [];
  const KEY = /^(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|([^\s#'"{}[\],&*!|>%@`-][^#]*?|-[^\s#][^#]*?))\s*:(?:\s|$)/;
  text.split("\n").forEach((raw, i) => {
    if (/^\s*(#|$)/.test(raw)) return;
    const indent = raw.length - raw.trimStart().length;
    while (frames.length && frames[frames.length - 1].indent >= indent) frames.pop();
    let rest = raw.slice(indent);
    let at = indent;
    const dash = /^-(\s+|$)/.exec(rest);
    if (dash) {
      frames.push({ indent, path: null });
      rest = rest.slice(dash[0].length);
      at = indent + dash[0].length;
    }
    const m = KEY.exec(rest);
    if (!m) return;
    const key = m[1] ?? m[2] ?? m[3];
    const top = frames[frames.length - 1];
    const path = top === undefined ? key : top.path === null ? null : `${top.path}.${key}`;
    if (path !== null && !out.has(path)) out.set(path, i + 1);
    frames.push({ indent: at, path });
  });
  return out;
}

/** The line `path` (dotted, as a problem names it) is written on in `text` —
 * or its deepest part the text has, since a problem can name a key the patch
 * leaves to the default. null when none of it is there, or the text does not
 * parse. Block YAML is the page's form (M9); flow / JSON text still reads. */
export function lineOfPath(text: string, path: string): number | null {
  if (!parsePatch(text).ok) return null;
  const lines = text.trimStart().startsWith("{") ? jsonKeyLines(text) : yamlKeyLines(text);
  const parts = path.split(".");
  for (let n = parts.length; n > 0; n--) {
    const found = lines.get(parts.slice(0, n).join("."));
    if (found !== undefined) return found;
  }
  return null;
}

export type Parsed = { ok: true; patch: Patch } | { ok: false; error: string; line?: number };

/** The editor's text as a patch — YAML, as `config.yaml` is written (M9; JSON,
 * being YAML, reads too). The core schema, so a date-like value stays text.
 * `error` is the parser's own words (with the line it names), or `not-an-object`. */
export function parsePatch(text: string): Parsed {
  if (text.trim() === "") return { ok: true, patch: {} };
  let value: unknown;
  try {
    value = loadYaml(text, { schema: CORE_SCHEMA });
  } catch (e) {
    const { reason, mark } = e as { reason?: string; mark?: { line?: number } };
    const error = reason ?? (e instanceof Error ? e.message : String(e));
    return mark?.line === undefined ? { ok: false, error } : { ok: false, error, line: mark.line + 1 };
  }
  return isObject(value) ? { ok: true, patch: value } : { ok: false, error: "not-an-object" };
}

/** A patch as the page writes it: block YAML, as `config.yaml` is (M9). */
export function dumpPatch(patch: unknown): string {
  return dumpYaml(patch, { schema: CORE_SCHEMA, lineWidth: -1, noRefs: true });
}
