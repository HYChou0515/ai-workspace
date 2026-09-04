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
import { RAW_DOC, type SortRule, type ViewKind, type ViewSpec } from "./types";

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
  const spec: ViewSpec = {
    // A plug-in's own keys ride through untouched at RUNTIME — they are its
    // config. They are deliberately NOT on `ViewSpec` (an index signature there
    // disarmed typo-checking for every named field), so a plug-in reads them
    // with `viewParam` / `viewParamString`, which go to the original document.
    ...(o as ViewSpec),
    // ...while the fields listed below are coerced, because this document is
    // arbitrary user YAML. Widening which files parse (#698) without widening
    // this was a real defect: `title:` as a mapping reached a React child and
    // threw, and with no error boundary above it that blanks the page.
    //
    // This is an enumeration, not a schema — it covers what is listed here and
    // nothing else. `color_by`, `always_week`, `weekday` and `day_of_month` are
    // still carried raw (they degrade quietly rather than throwing). Adding a
    // field to `ViewSpec` means adding it here too; see renderers/README.md.
    view: view as ViewKind,
    entity: str(entity) ?? "",
    title: str(o.title),
    group_by: str(o.group_by),
    span: str(o.span),
    label: str(o.label),
    assignee: str(o.assignee),
    assignee_display: assigneeDisplay(o.assignee_display),
    skip_weekends: typeof o.skip_weekends === "boolean" ? o.skip_weekends : undefined,
    work_hours: normalizeWorkHours(o.work_hours),
    columns: normalizeStringList(o.columns),
    card: normalizeCard(o.card),
    week: normalizeWeek(o.week),
    schedule: normalizeSchedule(o.schedule),
    sort: normalizeSort(o.sort),
    hidden_fields: normalizeStringList(o.hidden_fields),
    // rides along through every `{...spec}` the container does
    [RAW_DOC]: o,
  };
  return spec;
}

/** One end of a working-hours window: `"07:00"` → 7, `"08:30"` → 8.5. A bare
 * number is accepted too, because `from: 7` is what a person writes when the
 * hour is whole and YAML would hand it over unquoted anyway. */
export function clockHours(raw: unknown): number | undefined {
  if (typeof raw === "number") return Number.isFinite(raw) && raw >= 0 && raw <= 24 ? raw : undefined;
  const m = /^(\d{1,2}):([0-5]\d)$/.exec(String(raw).trim());
  if (!m) return undefined;
  const h = Number(m[1]);
  return h <= 24 ? h + Number(m[2]) / 60 : undefined;
}

/** The inverse of {@link clockHours}: 7 → `"07:00"`, 8.5 → `"08:30"`. Kept
 * beside it deliberately — a formatter that drifts from its parser writes view
 * files the parser then drops, and it drops them silently. */
export function clockText(hours: number): string {
  const total = Math.round(hours * 60);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

/** `work_hours:` is the part of the day the chart draws — the weekend rule at a
 * finer scale.
 *
 * Dropped WHOLE unless both ends parse and the window actually contains time.
 * Half a window, or one that closes before it opens, folds every hour of every
 * day away: bars go to zero columns and the chart renders blank. A view file
 * with a typo in it should fall back to drawing the whole day, not to drawing
 * nothing — an empty timeline gives the reader nothing to diagnose from. */
function normalizeWorkHours(raw: unknown): ViewSpec["work_hours"] {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const o = raw as Record<string, unknown>;
  const from = clockHours(o.from);
  const to = clockHours(o.to);
  if (from === undefined || to === undefined || from >= to) return undefined;
  return { from, to };
}

/** `week:` drives the gantt's time axis, and every field in it is a scalar the
 * chart formats or matches on — a `label:` left blank (which the shipped file
 * documents as an option) parsed as `null` and threw inside `formatWeekLabel`,
 * because the default only fires for `undefined`. */
function normalizeWeek(raw: unknown): ViewSpec["week"] {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const o = raw as Record<string, unknown>;
  // Coerce the TYPE, then check the DOMAIN: `week: {start: mon}` survives a
  // string check and then indexes a weekday table with a miss, producing
  // `new Date(NaN)` and a bare "Invalid time value" from deep inside the axis.
  // These are small closed enums written by hand in a YAML file; an unknown
  // member is a typo, and falling back to the default beats a stack trace.
  return {
    start: oneOf(o.start, WEEKDAYS),
    first_week: oneOf(o.first_week, ["jan1", "first_full", "iso"]),
    reset: oneOf(o.reset, ["yearly", "none"]),
    boundary: oneOf(o.boundary, ["new_year", "old_year", "by_today"]),
    // Not an enum, but still fed to `Date.parse` — `epoch: yesterday` produced
    // the same bare "Invalid time value" from inside the axis that `start: mon`
    // did. Domain-checking one field and not its neighbour just moves the crash.
    epoch: date(o.epoch),
    label: str(o.label),
  } as ViewSpec["week"];
  // NOTE: an empty `week: {}` returns an empty rule, NOT undefined. Every field
  // has a default, and all three shipped gantt files tell the user in their own
  // comments that "a bare `week: {}` is already a valid rule" — dropping it
  // silently cost the timeline its W-codes for anyone who took them at
  // their word, or who commented the block's contents out.
}

/** Read a key the platform doesn't know about — a plug-in kind's own config,
 * e.g. `viewParam(spec, "source")` for `source: /data/wafer.csv` (#698).
 *
 * This exists so `ViewSpec` does NOT need an index signature. With one, every
 * named field lost its typo check and every `ViewSpec` literal lost excess-
 * property checking; the cost landed on the whole codebase to serve plug-ins.
 * Here the cast is in one audited place and the caller still has to narrow.
 *
 * It reads the ORIGINAL document (carried on the spec under `RAW_DOC`) so a
 * plug-in key that collides with a platform one — `columns`, `card`, `sort`,
 * `label`, … — still reads back the way its author wrote it, rather than the
 * platform's coerced, often dropped, version. */
export function viewParam(spec: ViewSpec, key: string): unknown {
  const raw = spec[RAW_DOC];
  if (raw && Object.hasOwn(raw, key)) return raw[key];
  return (spec as unknown as Record<string, unknown>)[key];
}

/** Same, for the common case: a string value, or undefined if it isn't one. */
export function viewParamString(spec: ViewSpec, key: string): string | undefined {
  return str(viewParam(spec, key));
}

/** A YAML scalar the platform will render or match on. Numbers and booleans are
 * stringified rather than dropped — `title: 2026` is a perfectly ordinary thing
 * to write, and silently discarding it is a regression, not safety. What must
 * not survive is a mapping or a list, which is what reached a React child and
 * blanked the page. */
function str(raw: unknown): string | undefined {
  if (typeof raw === "string") return raw || undefined;
  if (typeof raw === "number" || typeof raw === "boolean") return String(raw);
  return undefined;
}

function assigneeDisplay(raw: unknown): ViewSpec["assignee_display"] {
  return raw === "avatar" || raw === "name" || raw === "none" ? raw : undefined;
}

/** `schedule:` names the FIELDS the auto-scheduler writes, so a half-written
 * block is worse than none: with `span:` missing, Recalculate used to PATCH a
 * field literally named "undefined" onto every record in one click. All three
 * required names must be present, or the view is simply not a scheduling one and
 * the Recalculate affordance never appears. */
function normalizeSchedule(raw: unknown): ViewSpec["schedule"] {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const o = raw as Record<string, unknown>;
  const span = str(o.span);
  const duration = str(o.duration);
  const flag = str(o.flag);
  if (!span || !duration || !flag) return undefined;
  return { span, duration, flag, unit: str(o.unit), anchor: str(o.anchor), assignee: str(o.assignee) };
}

const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];

/** A scalar that must be one of a small closed set, or nothing. Coercing the
 * type alone isn't enough for a field that then indexes a lookup table. */
function oneOf(raw: unknown, allowed: string[]): string | undefined {
  const s = str(raw);
  return s !== undefined && allowed.includes(s) ? s : undefined;
}

/** A scalar that must be a parseable date, or nothing — same reasoning as
 * `oneOf`, for a field consumed by `Date.parse` rather than a lookup table. */
function date(raw: unknown): string | undefined {
  const s = str(raw);
  return s !== undefined && !Number.isNaN(Date.parse(s)) ? s : undefined;
}

/** `card:` drives the board's title + badges, both rendered — so both are
 * coerced, and a non-object `card:` is dropped rather than dereferenced. */
function normalizeCard(raw: unknown): ViewSpec["card"] {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const o = raw as Record<string, unknown>;
  const card = { title: str(o.title), badges: normalizeStringList(o.badges) };
  return card.title === undefined && card.badges === undefined ? undefined : card;
}

/** Set (or remove) a TOP-LEVEL scalar `key` in a view file's raw YAML, preserving
 * every comment and the rest of the layout: replace the value when the key exists,
 * append it when absent, or drop the whole line when `value` is null. The gantt
 * gear persists its toggles through this rather than a YAML parse+dump (which would
 * strip the self-documenting `week:` comments). */
export function setViewScalar(text: string, key: string, value: string | null): string {
  const esc = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const head = new RegExp(`^(\\s*)${esc}\\s*:`);
  const lines = text.split("\n");
  const at = lines.findIndex((l) => head.test(l));
  if (at === -1) {
    return value === null ? text : `${text.replace(/\s*$/, "")}\n${key}: ${value}\n`;
  }
  // A key whose value is a nested mapping OWNS the indented lines under it, and
  // touching only its own line leaves those orphaned beneath a flow mapping.
  // That is not a wrong setting but a YAML parse error, and `parseViewSpec`
  // answers those with null — so the whole view would go blank because someone
  // moved a control. Stops at the first blank line or the first line back at
  // this key's own indent, which is where the block ends.
  const indent = (head.exec(lines[at]) as RegExpExecArray)[1].length;
  let end = at + 1;
  while (end < lines.length && lines[end].trim() !== "") {
    if ((/^\s*/.exec(lines[end]) as RegExpExecArray)[0].length <= indent) break;
    end++;
  }
  const rest = lines.slice(end);
  const replacement = value === null ? [] : [`${lines[at].slice(0, lines[at].indexOf(":") + 1)} ${value}`];
  return [...lines.slice(0, at), ...replacement, ...rest].join("\n");
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
