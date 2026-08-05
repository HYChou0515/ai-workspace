/**
 * Cross-renderer helpers shared by the entity view kinds (#419 §B / #448 P1):
 * value formatting, span parsing, the view-spec parser, and role lookup. Kept in
 * one module so `TableView`, `BoardView`, and `GanttView` can each live in their
 * own file without one importing another. The editable widgets live in
 * `roleWidget` (the single role→widget table).
 *
 * The 409 conflict banner (§B2) lives here too, because every surface that can
 * write a record has to be able to say "your edit didn't land": the view shell,
 * the record file tab, and the #680 modal. It was private to `EntityViews` while
 * there was one caller, and the file tab had grown its own inline copy of the
 * same sentence — two copies of one rule is how the two drift apart.
 */

import { load as parseYaml } from "js-yaml";

import type { EntityFieldSpec, EntityType } from "../../api/entities";
import type { SortRule, ViewKind, ViewSpec } from "./types";

/** Normalise a raw `sort:` value into clean `SortRule[]` — keep only entries with
 * a non-empty `field`, default `dir` to "asc", drop anything malformed. Returns
 * `undefined` when nothing usable is left, so the view falls back to manual
 * `rank` order (#GH-projects). Hand-edited YAML can't crash the sort. */
export function normalizeSort(raw: unknown): SortRule[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const rules: SortRule[] = [];
  for (const r of raw) {
    if (!r || typeof r !== "object") continue;
    const { field, dir } = r as Record<string, unknown>;
    if (typeof field !== "string" || !field) continue;
    rules.push({ field, dir: dir === "desc" ? "desc" : "asc" });
  }
  return rules.length > 0 ? rules : undefined;
}

/** Keep only the string entries of a raw list (e.g. `hidden_fields:`), or
 * `undefined` when none — so a malformed value degrades to "nothing hidden". */
export function normalizeStringList(raw: unknown): string[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out = raw.filter((x): x is string => typeof x === "string" && x.length > 0);
  return out.length > 0 ? out : undefined;
}

/** Parse a `views/*.ai.yaml` doc into a `ViewSpec`, or `null` when it isn't a
 * view file at all (bad YAML, or no `view:` naming a kind). Never throws — the
 * container degrades to the structured YAML tree on `null` (§E).
 *
 * This answers ONE question — "is this a view file?" — and nothing else (#698).
 * It deliberately does NOT decide:
 *
 *   - whether the kind EXISTS. That is the registry's business; an unknown kind
 *     parses fine so the dispatcher can render "Unsupported view kind: x". The
 *     old hardcoded whitelist here made the registry's documented fallback
 *     unreachable, and meant a second-party kind could never load.
 *   - whether an `entity:` is REQUIRED. That is a property of the kind
 *     (`ViewRenderer.needsEntity`), enforced by the dispatcher. A plug-in kind
 *     that reads workspace files has no entity, and the old rule — required for
 *     everything except a hardcoded `health` — made that unrepresentable.
 *
 * Unknown top-level keys ride through verbatim (the spread below), which is how
 * a plug-in reads its own config, e.g. `source: /data/wafer.csv`. */
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
  if (typeof view !== "string" || !view) return null;
  const sort = normalizeSort(o.sort);
  const hidden_fields = normalizeStringList(o.hidden_fields);
  return {
    ...(o as ViewSpec),
    view: view as ViewKind,
    entity: typeof entity === "string" ? entity : "",
    sort,
    hidden_fields,
  };
}

/** Set (or remove) a TOP-LEVEL scalar `key` in a view file's raw YAML, preserving
 * every comment and the rest of the layout: replace the value when the key exists,
 * append it when absent, or drop the whole line when `value` is null. The gantt
 * gear persists its toggles through this rather than a YAML parse+dump (which would
 * strip the self-documenting `week:` comments). */
export function setViewScalar(text: string, key: string, value: string | null): string {
  const esc = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (value === null) {
    return text.replace(new RegExp(`^\\s*${esc}\\s*:.*(\\r?\\n)?`, "m"), "");
  }
  const line = new RegExp(`^(\\s*${esc}\\s*:).*$`, "m");
  if (line.test(text)) return text.replace(line, `$1 ${value}`);
  return `${text.replace(/\s*$/, "")}\n${key}: ${value}\n`;
}

/** Convenience for the boolean `skip_weekends` flag. */
export function setSkipWeekendsInYaml(text: string, next: boolean): string {
  return setViewScalar(text, "skip_weekends", String(next));
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

// ── conflict banner (§B2) ──────────────────────────────────────────────────

/** A non-blocking alert for records whose optimistic-lock write hit a 409. The
 * write hook has already reloaded the row to the other person's value; this just
 * tells the user their edit didn't land and lets them dismiss per record. */
export function ConflictBanner({ conflicts, onDismiss }: { conflicts: number[]; onDismiss?: (number: number) => void }) {
  return (
    <div role="alert" className="ev-banner">
      <span className="ev-banner__icon" aria-hidden>
        ⚠
      </span>
      <div className="ev-banner__body">
        Someone else changed {conflicts.length === 1 ? "this record" : "these records"} — your edit wasn't applied and the
        latest {conflicts.length === 1 ? "value was" : "values were"} reloaded.
        <span className="ev-banner__actions">
          {conflicts.map((n) => (
            <button
              key={n}
              type="button"
              className="ev-banner__chip"
              aria-label={`dismiss conflict ${n}`}
              onClick={() => onDismiss?.(n)}
            >
              #{n} ✕
            </button>
          ))}
        </span>
      </div>
    </div>
  );
}
