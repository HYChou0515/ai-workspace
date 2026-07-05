/**
 * Cross-renderer helpers shared by the entity view kinds (#419 §B / #448 P1):
 * value formatting, span parsing, the view-spec parser, and role lookup. Kept in
 * one module so `TableView`, `BoardView`, and `GanttView` can each live in their
 * own file without one importing another. The editable widgets live in
 * `roleWidget` (the single role→widget table).
 */

import { load as parseYaml } from "js-yaml";

import type { EntityFieldSpec, EntityType } from "../../api/entities";
import type { ViewKind, ViewSpec } from "./types";

/** Parse a `views/*.ai.yaml` doc into a `ViewSpec`, or `null` when it isn't a
 * well-formed view (bad YAML, missing/unknown `view`, or — for the record-bound
 * kinds — no `entity`). The cross-type `health` view needs no `entity`. Never
 * throws — the container degrades to the raw text editor on `null` (§E). */
export function parseViewSpec(text: string): ViewSpec | null {
  let doc: unknown;
  try {
    doc = parseYaml(text);
  } catch {
    return null;
  }
  if (!doc || typeof doc !== "object") return null;
  const o = doc as Record<string, unknown>;
  const { view, entity } = o;
  if (view !== "table" && view !== "board" && view !== "gantt" && view !== "health") return null;
  if (view !== "health" && (typeof entity !== "string" || !entity)) return null;
  return { ...(o as ViewSpec), view: view as ViewKind, entity: (entity as string) ?? "" };
}

// ── value formatting ───────────────────────────────────────────────────────

export function fieldText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.map(fieldText).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Parse a `daterange` value (`"start/end"` string, `[start, end]`, or
 * `{start,end}` / `{from,to}`) into two epoch millis, or `null`. */
export function parseSpan(value: unknown): { start: number; end: number } | null {
  let a: unknown;
  let b: unknown;
  if (typeof value === "string" && value.includes("/")) {
    [a, b] = value.split("/", 2);
  } else if (Array.isArray(value) && value.length === 2) {
    [a, b] = value;
  } else if (value && typeof value === "object") {
    const o = value as Record<string, unknown>;
    a = o.start ?? o.from;
    b = o.end ?? o.to;
  } else {
    return null;
  }
  const start = Date.parse(String(a));
  const end = Date.parse(String(b));
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  return { start, end };
}

export function roleOf(type: EntityType | null, name: string): EntityFieldSpec | undefined {
  return type?.fields.find((f) => f.name === name);
}
