/**
 * role → widget — the single source of truth (#448 P3, §B3). Every surface that
 * edits an entity field (table inline, board card, quick-create, the file
 * editor) resolves its control here, so a role always looks + behaves the same:
 *
 *   text → text · status → dropdown (closed `values`) · actor → directory select
 *   date → date · daterange → start/end · progress/rank → number · ref → number
 *   (a proper #N-picker lands in P4) · backref/rollup → read-only (compute-on-read)
 *
 * `RoleField` is the inline editor (uncontrolled scalars commit on blur; discrete
 * widgets commit on change). `RoleCreateInput` is the controlled quick-create
 * variant. Both share the discrete `StatusSelect` / `ActorSelect` /
 * `DateRangeInput`, so widgets never drift between create + edit.
 */

import { useState } from "react";

import type { EntityRole } from "../../api/entities";
import type { User } from "../../api/types";
import type { RefOption } from "./refTraversal";
import { fieldText } from "./shared";

export type WidgetKind =
  | "text"
  | "select"
  | "actor"
  | "date"
  | "daterange"
  | "progress"
  | "rank"
  | "ref"
  | "readonly";

const ROLE_WIDGET: Record<EntityRole, WidgetKind> = {
  text: "text",
  status: "select",
  actor: "actor",
  date: "date",
  daterange: "daterange",
  progress: "progress",
  rank: "rank",
  ref: "ref",
  // compute-on-read — derived from other records, so there's nothing to write.
  backref: "readonly",
  rollup: "readonly",
};

export function widgetForRole(role: EntityRole): WidgetKind {
  return ROLE_WIDGET[role];
}

const NUMERIC: ReadonlySet<WidgetKind> = new Set<WidgetKind>(["progress", "rank", "ref"]);

// ── shared discrete widgets (used by both create + edit) ─────────────────────

function StatusSelect({
  name,
  value,
  values,
  blank,
  disabled,
  required,
  className,
  onCommit,
}: {
  name: string;
  value: unknown;
  values?: string[] | null;
  blank?: boolean;
  disabled?: boolean;
  required?: boolean;
  className?: string;
  onCommit: (next: string) => void;
}) {
  const text = fieldText(value);
  const known = values ?? [];
  return (
    <select
      aria-label={name}
      className={className}
      value={text}
      disabled={disabled}
      required={required}
      onChange={(e) => onCommit(e.target.value)}
    >
      {blank && <option value="">—</option>}
      {/* keep an out-of-vocabulary current value visible (a lint warning, §D) */}
      {!blank && !known.includes(text) && <option value={text}>{text || "—"}</option>}
      {known.map((v) => (
        <option key={v} value={v}>
          {v}
        </option>
      ))}
    </select>
  );
}

function ActorSelect({
  name,
  value,
  users,
  disabled,
  required,
  className,
  onCommit,
}: {
  name: string;
  value: unknown;
  users?: User[];
  disabled?: boolean;
  required?: boolean;
  className?: string;
  onCommit: (next: string) => void;
}) {
  const text = fieldText(value);
  const directory = users ?? [];
  return (
    <select
      aria-label={name}
      className={className}
      value={text}
      disabled={disabled}
      required={required}
      onChange={(e) => onCommit(e.target.value)}
    >
      <option value="">—</option>
      {/* an assignee not in the directory stays selectable so it isn't dropped */}
      {text && !directory.some((u) => u.id === text) && <option value={text}>{text}</option>}
      {directory.map((u) => (
        <option key={u.id} value={u.id}>
          {u.name || u.id}
        </option>
      ))}
    </select>
  );
}

/** Split a `daterange` value into its raw start/end date strings (no epoch
 * coercion — the `<input type=date>` wants `YYYY-MM-DD`). */
function splitRange(value: unknown): { start: string; end: string } {
  if (typeof value === "string" && value.includes("/")) {
    const [a, b] = value.split("/", 2);
    return { start: a ?? "", end: b ?? "" };
  }
  if (Array.isArray(value) && value.length === 2) {
    return { start: String(value[0] ?? ""), end: String(value[1] ?? "") };
  }
  if (value && typeof value === "object") {
    const o = value as Record<string, unknown>;
    return { start: String(o.start ?? o.from ?? ""), end: String(o.end ?? o.to ?? "") };
  }
  return { start: "", end: "" };
}

function DateRangeInput({
  name,
  value,
  disabled,
  className,
  onCommit,
}: {
  name: string;
  value: unknown;
  disabled?: boolean;
  className?: string;
  onCommit: (next: unknown) => void;
}) {
  const init = splitRange(value);
  const [start, setStart] = useState(init.start);
  const [end, setEnd] = useState(init.end);
  // Only write a whole range; a half-filled range holds locally (no partial patch).
  const emit = (s: string, e: string) => {
    if (s && e) onCommit(`${s}/${e}`);
    else if (!s && !e) onCommit(null);
  };
  return (
    <span style={{ display: "inline-flex", gap: 4 }}>
      <input
        aria-label={`${name} start`}
        className={className}
        type="date"
        value={start}
        disabled={disabled}
        onChange={(e) => {
          setStart(e.target.value);
          emit(e.target.value, end);
        }}
      />
      <input
        aria-label={`${name} end`}
        className={className}
        type="date"
        value={end}
        disabled={disabled}
        onChange={(e) => {
          setEnd(e.target.value);
          emit(start, e.target.value);
        }}
      />
    </span>
  );
}

// ── inline editor (table / board): commit on blur, discrete on change ────────

const inlineInputStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  background: "transparent",
  border: "none",
};

/** A ref rendered as a `#N <title>` picker over the target records, or a plain
 * numeric input when the target records aren't loaded (P4 fallback). */
function RefSelect({
  name,
  value,
  options,
  disabled,
  required,
  className,
  onCommit,
}: {
  name: string;
  value: unknown;
  options: RefOption[];
  disabled?: boolean;
  required?: boolean;
  className?: string;
  onCommit: (next: unknown) => void;
}) {
  const text = fieldText(value);
  return (
    <select
      aria-label={name}
      className={className}
      value={text}
      disabled={disabled}
      required={required}
      onChange={(e) => onCommit(e.target.value === "" ? null : Number(e.target.value))}
    >
      <option value="">—</option>
      {text && !options.some((o) => String(o.number) === text) && <option value={text}>{text}</option>}
      {options.map((o) => (
        <option key={o.number} value={o.number}>
          #{o.number} {o.label}
        </option>
      ))}
    </select>
  );
}

export type RoleFieldProps = {
  widget: WidgetKind;
  name: string;
  value: unknown;
  values?: string[] | null;
  users?: User[];
  /** Target records for a `ref` widget — turns it into a #N-title picker. */
  refOptions?: RefOption[];
  disabled?: boolean;
  required?: boolean;
  onCommit: (next: unknown) => void;
};

export function RoleField({ widget, name, value, values, users, refOptions, disabled, required, onCommit }: RoleFieldProps) {
  if (widget === "readonly") return <span>{fieldText(value)}</span>;
  if (widget === "select")
    return <StatusSelect name={name} value={value} values={values} disabled={disabled} required={required} onCommit={onCommit} />;
  if (widget === "actor")
    return <ActorSelect name={name} value={value} users={users} disabled={disabled} required={required} onCommit={onCommit} />;
  if (widget === "daterange")
    return <DateRangeInput name={name} value={value} disabled={disabled} onCommit={onCommit} />;
  if (widget === "ref" && refOptions && refOptions.length > 0)
    return <RefSelect name={name} value={value} options={refOptions} disabled={disabled} required={required} onCommit={onCommit} />;

  const text = fieldText(value);
  const numeric = NUMERIC.has(widget);
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
      aria-label={name}
      type={numeric ? "number" : widget === "date" ? "date" : "text"}
      defaultValue={text}
      disabled={disabled}
      style={inlineInputStyle}
      onBlur={(e) => commit(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
    />
  );
}

// ── quick-create input (controlled draft) ────────────────────────────────────

export type RoleCreateInputProps = {
  widget: WidgetKind;
  name: string;
  value: string;
  values?: string[] | null;
  users?: User[];
  required?: boolean;
  onChange: (next: string) => void;
};

export function RoleCreateInput({ widget, name, value, values, users, required, onChange }: RoleCreateInputProps) {
  if (widget === "select")
    return (
      <StatusSelect name={name} value={value} values={values} blank required={required} className="inline-edit" onCommit={onChange} />
    );
  if (widget === "actor")
    return <ActorSelect name={name} value={value} users={users} required={required} className="inline-edit" onCommit={onChange} />;
  if (widget === "daterange")
    return <DateRangeInput name={name} value={value} className="inline-edit" onCommit={(next) => onChange(next == null ? "" : String(next))} />;

  const type = widget === "date" ? "date" : widget === "progress" || widget === "rank" || widget === "ref" ? "number" : "text";
  const placeholder = widget === "ref" ? "#" : "";
  return (
    <input
      className="inline-edit"
      aria-label={name}
      type={type}
      value={value}
      placeholder={placeholder}
      required={required}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
