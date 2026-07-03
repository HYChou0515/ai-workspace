/**
 * The declarative entity views (#419 §B) — pure presentational renderers for a
 * parsed view spec + its projected records. No network / hooks-with-IO here, so
 * the whole surface is testable with a plain render: the container
 * (`AiYamlRenderer`) resolves the spec + records + write handlers and passes
 * them in.
 *
 * A view spec is a small YAML doc (`view:` + `entity:` + per-view options) that
 * ships as a `views/*.ai.yaml` workspace file. Three kinds:
 *   - table — every record in a grid; status / progress / scalars edit inline.
 *   - board — records grouped into columns by a `status` field; a card's status
 *     select moves it between columns (the update write path).
 *   - gantt — records laid out as bars on a shared timeline from a `daterange`.
 */

import { useState } from "react";

import { load as parseYaml } from "js-yaml";

import type {
  EntityFieldSpec,
  EntityFormField,
  EntityHealthFinding,
  EntityInstance,
  EntityType,
} from "../../api/entities";
import { pxToRem } from "../../lib/pxToRem";

export type ViewKind = "table" | "board" | "gantt" | "health";

export type ViewSpec = {
  view: ViewKind;
  entity: string;
  title?: string;
  columns?: string[];
  group_by?: string;
  span?: string;
  label?: string;
  card?: { title?: string; badges?: string[] };
};

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

export type EntityViewProps = {
  spec: ViewSpec;
  /** The entity type from the catalog — supplies field roles + the create form.
   * `null` while the catalog is still loading (renders records read-only). */
  type: EntityType | null;
  entities: EntityInstance[];
  /** Records that failed to parse (shown as a degraded warning banner). */
  invalid?: EntityInstance[];
  onCreate: (args: Record<string, unknown>) => void;
  onPatch: (number: number, patch: Record<string, unknown>) => void;
  busy?: boolean;
};

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

function roleOf(type: EntityType | null, name: string): EntityFieldSpec | undefined {
  return type?.fields.find((f) => f.name === name);
}

const READONLY_ROLES = new Set(["backref", "rollup"]);

// ── inline-editable cell ───────────────────────────────────────────────────

function EditableCell({
  spec,
  value,
  disabled,
  onCommit,
}: {
  spec: EntityFieldSpec | undefined;
  value: unknown;
  disabled?: boolean;
  onCommit: (next: unknown) => void;
}) {
  const text = fieldText(value);
  const role = spec?.role;

  // Compute-on-read fields are never editable — they're derived from other
  // records, so there's nothing to write back.
  if (!spec || READONLY_ROLES.has(role ?? "")) {
    return <span>{text}</span>;
  }

  if (role === "status" && spec.values && spec.values.length > 0) {
    return (
      <select
        aria-label={spec.name}
        value={text}
        disabled={disabled}
        onChange={(e) => onCommit(e.target.value)}
      >
        {!spec.values.includes(text) && <option value={text}>{text || "—"}</option>}
        {spec.values.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    );
  }

  const numeric = role === "progress" || role === "rank" || role === "ref";
  const commit = (raw: string) => {
    const trimmed = raw.trim();
    if (trimmed === text) return;
    if (numeric) {
      if (trimmed === "") return onCommit(null);
      const n = Number(trimmed);
      if (!Number.isNaN(n)) onCommit(n);
      return;
    }
    onCommit(trimmed === "" ? null : trimmed);
  };

  return (
    // Keyed by the committed value so a successful patch remounts the input with
    // the fresh value (uncontrolled → no stale-draft bookkeeping).
    <input
      key={text}
      aria-label={spec.name}
      type={numeric ? "number" : role === "date" ? "date" : "text"}
      defaultValue={text}
      disabled={disabled}
      style={{ width: "100%", boxSizing: "border-box", background: "transparent", border: "none" }}
      onBlur={(e) => commit(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
    />
  );
}

// ── quick-create form ──────────────────────────────────────────────────────

function CreateInput({ field, value, onChange }: { field: EntityFormField; value: string; onChange: (v: string) => void }) {
  if (field.widget === "select" && field.values && field.values.length > 0) {
    return (
      <select aria-label={field.name} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {field.values.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    );
  }
  const type = field.widget === "date" ? "date" : field.widget === "progress" ? "number" : "text";
  const placeholder = field.widget === "daterange" ? "start/end" : field.widget === "ref" ? "#" : "";
  return (
    <input
      aria-label={field.name}
      type={type}
      value={value}
      placeholder={placeholder}
      required={field.required}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function QuickCreate({
  form,
  onCreate,
  busy,
}: {
  form: EntityFormField[];
  onCreate: (args: Record<string, unknown>) => void;
  busy?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)}>
        + New
      </button>
    );
  }

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const args: Record<string, unknown> = {};
    for (const f of form) {
      const v = (draft[f.name] ?? "").trim();
      if (v !== "") args[f.name] = v;
    }
    onCreate(args);
    setDraft({});
    setOpen(false);
  };

  return (
    <form onSubmit={submit} style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
      {form.map((f) => (
        <label key={f.name} style={{ display: "flex", flexDirection: "column", fontSize: pxToRem(12) }}>
          <span style={{ color: "var(--text-paper-d)" }}>
            {f.name}
            {f.required ? " *" : ""}
          </span>
          <CreateInput field={f} value={draft[f.name] ?? ""} onChange={(v) => setDraft((d) => ({ ...d, [f.name]: v }))} />
        </label>
      ))}
      <button type="submit" disabled={busy}>
        Create
      </button>
      <button type="button" onClick={() => setOpen(false)}>
        Cancel
      </button>
    </form>
  );
}

// ── table ──────────────────────────────────────────────────────────────────

function columnsFor(spec: ViewSpec, type: EntityType | null, entities: EntityInstance[]): string[] {
  if (spec.columns && spec.columns.length > 0) return spec.columns;
  if (type) return type.fields.map((f) => f.name);
  // No schema + no explicit columns → union of the records' own keys.
  const seen = new Set<string>();
  for (const e of entities) for (const k of Object.keys(e.fields)) seen.add(k);
  return [...seen];
}

function TableView({ spec, type, entities, onPatch, busy }: EntityViewProps) {
  const columns = columnsFor(spec, type, entities);
  return (
    <table style={{ borderCollapse: "collapse", width: "100%" }}>
      <thead>
        <tr>
          <th style={cellStyle}>#</th>
          {columns.map((c) => (
            <th key={c} style={cellStyle}>
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {entities.map((e) => (
          <tr key={e.number}>
            <td style={cellStyle}>{e.number}</td>
            {columns.map((c) => (
              <td key={c} style={cellStyle}>
                <EditableCell
                  spec={roleOf(type, c)}
                  value={e.fields[c]}
                  disabled={busy}
                  onCommit={(next) => onPatch(e.number, { [c]: next })}
                />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const cellStyle: React.CSSProperties = {
  border: "1px solid var(--line, #ccc)",
  padding: "4px 8px",
  textAlign: "left",
  verticalAlign: "top",
};

// ── board ──────────────────────────────────────────────────────────────────

function BoardView({ spec, type, entities, onPatch, busy }: EntityViewProps) {
  const groupField = spec.group_by ?? "status";
  const statusSpec = roleOf(type, groupField);
  const columnValues = statusSpec?.values ?? distinctValues(entities, groupField);
  const columns = [...columnValues, UNSET];
  const titleField = spec.card?.title ?? "title";
  const badges = spec.card?.badges ?? [];

  return (
    <div style={{ display: "flex", gap: 12, overflowX: "auto", alignItems: "flex-start" }}>
      {columns.map((col) => {
        const cards = entities.filter((e) => (fieldText(e.fields[groupField]) || UNSET) === col);
        if (col === UNSET && cards.length === 0) return null;
        return (
          <div key={col} style={{ minWidth: 180, flex: "0 0 auto" }}>
            <div data-testid={`col-${col === UNSET ? "unset" : col}`} style={{ fontWeight: 600, marginBottom: 6 }}>
              {col === UNSET ? "(unset)" : col} <span style={{ color: "var(--text-paper-d)" }}>{cards.length}</span>
            </div>
            {cards.map((e) => (
              <div key={e.number} style={{ border: "1px solid var(--line, #ccc)", borderRadius: 6, padding: 8, marginBottom: 8 }}>
                <div style={{ fontWeight: 500 }}>{fieldText(e.fields[titleField]) || `#${e.number}`}</div>
                {badges.map((b) => {
                  const bt = fieldText(e.fields[b]);
                  return bt ? (
                    <span key={b} style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)", marginRight: 8 }}>
                      {b}: {bt}
                    </span>
                  ) : null;
                })}
                {statusSpec?.values && (
                  <div style={{ marginTop: 6 }}>
                    <EditableCell
                      spec={statusSpec}
                      value={e.fields[groupField]}
                      disabled={busy}
                      onCommit={(next) => onPatch(e.number, { [groupField]: next })}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

const UNSET = " unset";

function distinctValues(entities: EntityInstance[], field: string): string[] {
  const seen = new Set<string>();
  for (const e of entities) {
    const v = fieldText(e.fields[field]);
    if (v) seen.add(v);
  }
  return [...seen];
}

// ── gantt ──────────────────────────────────────────────────────────────────

function GanttView({ spec, entities }: EntityViewProps) {
  const spanField = spec.span ?? "span";
  const labelField = spec.label ?? "title";
  const rows = entities
    .map((e) => ({ e, span: parseSpan(e.fields[spanField]) }))
    .filter((r): r is { e: EntityInstance; span: { start: number; end: number } } => r.span !== null);

  if (rows.length === 0) {
    return <div style={{ color: "var(--text-paper-d)" }}>No records with a date range to chart yet.</div>;
  }

  const min = Math.min(...rows.map((r) => r.span.start));
  const max = Math.max(...rows.map((r) => r.span.end));
  const scale = (t: number) => (max === min ? 0 : ((t - min) / (max - min)) * 100);

  return (
    <div>
      {rows.map(({ e, span }) => (
        <div key={e.number} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <div style={{ width: 160, flex: "0 0 auto", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {fieldText(e.fields[labelField]) || `#${e.number}`}
          </div>
          <div style={{ position: "relative", flex: 1, height: 18, background: "var(--paper-d, #f0f0f0)", borderRadius: 4 }}>
            <div
              data-testid={`bar-${e.number}`}
              title={fieldText(e.fields[spanField])}
              style={{
                position: "absolute",
                left: `${scale(span.start)}%`,
                width: `${Math.max(scale(span.end) - scale(span.start), 1)}%`,
                top: 0,
                bottom: 0,
                background: "var(--accent, #3B82F6)",
                borderRadius: 4,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

// ── dispatcher ─────────────────────────────────────────────────────────────

export function EntityViewBody(props: EntityViewProps) {
  const { spec, type, entities, invalid, onCreate, busy } = props;
  return (
    <div style={{ padding: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <h3 style={{ margin: 0 }}>{spec.title ?? spec.entity}</h3>
        {type && spec.view !== "gantt" && <QuickCreate form={type.form} onCreate={onCreate} busy={busy} />}
      </div>
      {invalid && invalid.length > 0 && (
        <div style={{ color: "var(--warn, #b8860b)", marginBottom: 8, fontSize: pxToRem(13) }}>
          {invalid.length} record{invalid.length > 1 ? "s" : ""} couldn't be parsed and {invalid.length > 1 ? "are" : "is"} hidden.
        </div>
      )}
      {entities.length === 0 && spec.view !== "gantt" ? (
        <div style={{ color: "var(--text-paper-d)" }}>No {spec.entity} records yet.</div>
      ) : spec.view === "table" ? (
        <TableView {...props} />
      ) : spec.view === "board" ? (
        <BoardView {...props} />
      ) : (
        <GanttView {...props} />
      )}
    </div>
  );
}

// ── health (§E3) ─────────────────────────────────────────────────────────────

/** The project-health view — a cross-type list of parser/lint findings. Not
 * part of `EntityViewBody` (it isn't bound to one entity type); the container
 * feeds it the health endpoint's findings directly. */
export function HealthView({ title, findings }: { title?: string; findings: EntityHealthFinding[] }) {
  const errors = findings.filter((f) => f.level === "error").length;
  const warnings = findings.length - errors;
  return (
    <div style={{ padding: 12 }}>
      <h3 style={{ margin: "0 0 10px" }}>{title ?? "Health"}</h3>
      {findings.length === 0 ? (
        <div style={{ color: "var(--ok, #2e7d32)" }}>All records are healthy — no findings.</div>
      ) : (
        <>
          <div style={{ marginBottom: 8, fontSize: pxToRem(13), color: "var(--text-paper-d)" }}>
            {errors} error{errors === 1 ? "" : "s"}, {warnings} warning{warnings === 1 ? "" : "s"}
          </div>
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {findings.map((f, i) => (
              <li
                key={`${f.type_name}-${f.number}-${i}`}
                style={{ display: "flex", gap: 8, padding: "4px 0", borderBottom: "1px solid var(--line, #eee)" }}
              >
                <span
                  style={{
                    color: f.level === "error" ? "var(--err, #c62828)" : "var(--warn, #b8860b)",
                    fontWeight: 600,
                    minWidth: 64,
                  }}
                >
                  {f.level}
                </span>
                <span style={{ minWidth: 90 }}>
                  {f.type_name} #{f.number}
                </span>
                <span>
                  {f.message}
                  {f.field ? ` (${f.field})` : ""}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
