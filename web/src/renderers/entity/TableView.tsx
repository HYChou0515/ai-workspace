/**
 * table view (#419 §B, #448 P5) — every record in a grid; status / progress /
 * scalar cells edit inline through the update write path. Column set comes from
 * the view spec (`columns`), else the schema's fields, else the union of record
 * keys. Sorting (header click), value filtering (status / actor domains), and
 * column show/hide are all local + ephemeral to the open panel ("本地即可").
 * Registered as the `table` kind in `viewKindRegistry`.
 */

import { Fragment, useEffect, useRef, useState } from "react";

import type { EntityFieldSpec, EntityInstance, EntityType } from "../../api/entities";
import type { User } from "../../api/types";
import { refOptions, type RefIndex, type RefOption, traverseColumn } from "./refTraversal";
import { RoleField, widgetForRole } from "./roleWidget";
import { selectColor } from "./selectColor";
import { fieldText, roleOf } from "./shared";
import { filterEntities, sortEntities, type SortDir } from "./tableOps";
import type { EntityViewProps, ViewSpec } from "./types";

function columnsFor(spec: ViewSpec, type: EntityType | null, entities: EntityInstance[]): string[] {
  if (spec.columns && spec.columns.length > 0) return spec.columns;
  // `rank` is manual-order infrastructure (#GH-projects), never a real column —
  // keep it out of the schema-derived default so it can't leak into the table.
  if (type) return type.fields.filter((f) => f.role !== "rank").map((f) => f.name);
  // No schema + no explicit columns → union of the records' own keys.
  const seen = new Set<string>();
  for (const e of entities) for (const k of Object.keys(e.fields)) seen.add(k);
  return [...seen];
}

type FilterOption = { value: string; label: string };

export function TableView({ spec, type, entities, invalid, users, refIndex, canWrite, onPatch, busy }: EntityViewProps) {
  const allColumns = columnsFor(spec, type, entities);
  const readOnly = canWrite === false; // §E — disable inline edits for non-writers
  const [sort, setSort] = useState<{ column: string; dir: SortDir } | null>(null);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const [menuOpen, setMenuOpen] = useState(false);

  const columns = allColumns.filter((c) => !hidden.has(c));
  const filtered = filterEntities(entities, filters, type ?? null, refIndex);
  const rows = sort ? sortEntities(filtered, sort.column, sort.dir, type ?? null, refIndex) : filtered;

  // click a header: none → asc → desc → none
  const cycleSort = (c: string) =>
    setSort((s) => (s?.column !== c ? { column: c, dir: "asc" } : s.dir === "asc" ? { column: c, dir: "desc" } : null));

  // A column is filterable when its role has a known value domain (§A1).
  const filterDomain = (c: string): FilterOption[] | null => {
    const fs = roleOf(type, c);
    if (fs?.role === "status") {
      const values = fs.values && fs.values.length > 0 ? fs.values : distinct(entities, c);
      return values.map((v) => ({ value: v, label: v }));
    }
    if (fs?.role === "actor") {
      return distinct(entities, c).map((id) => ({ value: id, label: users?.find((u) => u.id === id)?.name ?? id }));
    }
    return null;
  };
  const hasFilters = columns.some((c) => filterDomain(c));

  const toggleColumn = (c: string) =>
    setHidden((h) => {
      const next = new Set(h);
      if (next.has(c)) next.delete(c);
      else next.add(c);
      return next;
    });

  // ── multi-select + batch (§A1) ─────────────────────────────────────────────
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const visibleNumbers = rows.map((r) => r.number);
  const allSelected = visibleNumbers.length > 0 && visibleNumbers.every((n) => selected.has(n));
  const toggleRow = (n: number) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(visibleNumbers));

  // The closed-domain roles the issue calls out for batch edits (§A1).
  const batchFields = (type?.fields ?? []).filter((f) => f.role === "status" || f.role === "actor");
  const batchOptions = (f: EntityFieldSpec): FilterOption[] =>
    f.role === "status"
      ? (f.values ?? []).map((v) => ({ value: v, label: v }))
      : (users ?? []).map((u) => ({ value: u.id, label: u.name || u.id }));
  // No backend batch endpoint (§A1): fan out N single `update`s — each rides the
  // useEntityWrite conflict path, so a 409 on some rows shows in the shared
  // conflict banner while the rest still land ("部分成功 / 部分衝突").
  const applyBatch = (field: string, value: string) => {
    for (const n of selected) onPatch(n, { [field]: value });
  };

  // #GH-projects A — collapsible grouping when the view sets `group_by` (a
  // discrete field: status / assignee / milestone). Rows split into labelled
  // sections; the header resolves an actor→name, a ref→title, else the raw value.
  const groupField = spec.group_by;
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const toggleGroup = (key: string) =>
    setCollapsed((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const groups = groupField ? groupRows(rows, groupField, type, users, refIndex) : null;
  const colTotal = columns.length + 2; // + checkbox + #

  const renderRow = (e: EntityInstance) => {
    // A lint warning marks its field's cell yellow, still editable (§D).
    const warn = warningsByField(e.diagnostics);
    return (
      <tr key={e.number}>
        <td className="ev-table__check">
          {!readOnly && (
            <input
              type="checkbox"
              aria-label={`select ${e.number}`}
              checked={selected.has(e.number)}
              onChange={() => toggleRow(e.number)}
            />
          )}
        </td>
        <td className="ev-table__num">{e.number}</td>
        {columns.map((c) => {
          const warnMsg = warn[c];
          const td = warnMsg ? { className: "ev-table__cell--warn", title: warnMsg } : {};
          const traversal = refIndex ? traverseColumn(c, e, type, refIndex) : null;
          if (traversal) {
            return (
              <td key={c} {...td}>
                {traversal.dangling ? (
                  <span title="referenced record not found" style={{ color: "var(--warn)" }}>
                    {traversal.text}
                  </span>
                ) : (
                  traversal.text
                )}
              </td>
            );
          }
          const fieldSpec = roleOf(type, c);
          const opts = fieldSpec?.role === "ref" && refIndex ? refOptions(fieldSpec, refIndex) : undefined;
          return (
            <td key={c} {...td}>
              <EditableCell
                column={c}
                fieldSpec={fieldSpec}
                value={e.fields[c]}
                users={users}
                refOptions={opts}
                disabled={busy || readOnly}
                onCommit={(next) => onPatch(e.number, { [c]: next })}
              />
            </td>
          );
        })}
      </tr>
    );
  };

  return (
    // `min-width: 0` so this flex child of `.ev-panel` can shrink below the
    // table's min-content width — otherwise the flex item's automatic minimum
    // stretches the whole panel past the pane and the last columns (DUE /
    // PROGRESS) get clipped at the pane edge instead of scrolling inside the
    // bordered table wrap (#3 "text cut").
    <div className="ev-tableview">
      <div className="ev-toolbar" style={{ position: "relative", marginBottom: 8 }}>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          onClick={() => setMenuOpen((o) => !o)}
        >
          Columns
        </button>
        {menuOpen && (
          <div role="menu" className="ev-menu" style={{ top: "100%" }}>
            {allColumns.map((c) => (
              <label key={c} className="ev-menu__item">
                <input type="checkbox" aria-label={`toggle ${c}`} checked={!hidden.has(c)} onChange={() => toggleColumn(c)} /> {c}
              </label>
            ))}
          </div>
        )}
      </div>

      {selected.size > 0 && (
        <div role="toolbar" aria-label="batch actions" className="ev-toolbar" style={{ marginBottom: 8 }}>
          <span className="ev-toolbar__meta">{selected.size} selected</span>
          {batchFields.map((f) => (
            <label key={f.name} className="ev-health__filter">
              {f.name}:{" "}
              <select
                className="ev-select"
                aria-label={`batch ${f.name}`}
                value=""
                onChange={(e) => {
                  if (e.target.value !== "") applyBatch(f.name, e.target.value);
                }}
              >
                <option value="">— set —</option>
                {batchOptions(f).map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          ))}
          <button type="button" className="btn" data-variant="ghost" data-size="sm" onClick={() => setSelected(new Set())}>
            Clear selection
          </button>
        </div>
      )}

      <div className="ev-table-wrap scrollable">
        <table className="ev-table">
          <thead>
            <tr>
              <th className="ev-table__check">
                {/* §E — no multi-select / batch for a read-only member. */}
                {!readOnly && (
                  <input type="checkbox" aria-label="select all" checked={allSelected} onChange={toggleAll} />
                )}
              </th>
              <th className="ev-table__num">#</th>
              {columns.map((c) => (
                <th key={c}>
                  <button
                    type="button"
                    className="ev-table__sort"
                    data-sorted={sort?.column === c ? "" : undefined}
                    onClick={() => cycleSort(c)}
                  >
                    {c}
                    {sort?.column === c && <span className="ev-table__arrow">{sort.dir === "asc" ? "▲" : "▼"}</span>}
                  </button>
                </th>
              ))}
            </tr>
            {hasFilters && (
              <tr className="ev-table__filters">
                <th />
                <th />
                {columns.map((c) => {
                  const domain = filterDomain(c);
                  return (
                    <th key={c}>
                      {domain && (
                        <select
                          className="ev-select"
                          aria-label={`filter ${c}`}
                          value={filters[c] ?? ""}
                          onChange={(e) => setFilters((f) => ({ ...f, [c]: e.target.value }))}
                        >
                          <option value="">All</option>
                          {domain.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </select>
                      )}
                    </th>
                  );
                })}
              </tr>
            )}
          </thead>
          <tbody>
            {groups
              ? groups.map((g) => {
                  const open = !collapsed.has(g.key);
                  return (
                    <Fragment key={g.key}>
                      <tr className="ev-table__group">
                        <td colSpan={colTotal}>
                          <button
                            type="button"
                            className="ev-table__group-btn"
                            aria-expanded={open}
                            data-testid={`group-${g.key}`}
                            onClick={() => toggleGroup(g.key)}
                          >
                            <span className="ev-table__group-caret" aria-hidden>
                              {open ? "▾" : "▸"}
                            </span>
                            {g.chip}
                            <span className="ev-table__group-count">{g.rows.length}</span>
                          </button>
                        </td>
                      </tr>
                      {open && g.rows.map(renderRow)}
                    </Fragment>
                  );
                })
              : rows.map(renderRow)}
            {/* Unparseable records degrade to an error row (§D) — never dropped
                silently; the raw body shows so the fix is visible. */}
            {(invalid ?? []).map((e) => (
              <tr key={`invalid-${e.number}`} className="ev-table__row--error">
                <td className="ev-table__check" />
                <td className="ev-table__num">#{e.number}</td>
                <td colSpan={Math.max(columns.length, 1)}>
                  {e.diagnostics
                    .filter((d) => d.level === "error")
                    .map((d) => d.message)
                    .join("; ") || "unparseable record"}
                  {e.body ? ` — ${e.body.slice(0, 80)}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** A table value cell. To keep the grid narrow + readable it shows the value as
 * plain text at rest and swaps to the shared `RoleField` editor only on click /
 * focus (#3): a row of always-on native selects + date pickers made the table
 * far wider than its pane, pushing the last columns (DUE / PROGRESS) off-screen.
 * Compute-on-read / read-only cells stay text (never editable). Discrete widgets
 * (select / actor / ref) close on pick; the rest close when focus leaves. */
function EditableCell({
  column,
  fieldSpec,
  value,
  users,
  refOptions: opts,
  disabled,
  onCommit,
}: {
  column: string;
  fieldSpec: EntityFieldSpec | undefined;
  value: unknown;
  users?: User[];
  refOptions?: RefOption[];
  disabled?: boolean;
  onCommit: (next: unknown) => void;
}) {
  const widget = fieldSpec ? widgetForRole(fieldSpec.role) : "readonly";
  const [editing, setEditing] = useState(false);
  const name = fieldSpec?.name ?? column;
  const display = cellDisplay(fieldSpec, value, users, opts);

  // Compute-on-read (backref/rollup) or a read-only member: text, never editable.
  if (widget === "readonly" || disabled) {
    return <span className="ev-readonly">{display}</span>;
  }

  if (!editing) {
    // A single-select value shows as a coloured chip (GitHub-Projects style); the
    // cell still opens the select on click.
    const face =
      widget === "select" && display ? (
        <SelectChip value={display} fieldSpec={fieldSpec} />
      ) : (
        display || <span className="ev-cell__empty">—</span>
      );
    return (
      <button type="button" className="ev-cell" aria-label={`edit ${name}`} onClick={() => setEditing(true)}>
        {face}
      </button>
    );
  }

  // Native selects commit onChange with focus intact → close on pick. Text /
  // date / daterange commit on blur, so closing on blur covers them.
  const closeOnCommit = widget === "select" || widget === "actor" || widget === "ref";
  return (
    <span
      className="ev-cell__edit"
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setEditing(false);
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") setEditing(false);
      }}
    >
      <AutoFocus>
        <RoleField
          widget={widget}
          name={name}
          value={value}
          values={fieldSpec?.values}
          users={users}
          refOptions={opts}
          onCommit={(next) => {
            onCommit(next);
            if (closeOnCommit) setEditing(false);
          }}
        />
      </AutoFocus>
    </span>
  );
}

/** Focus the first control the moment a cell enters edit mode, so a click on the
 * text cell lands the caret / opens the picker without a second click. */
function AutoFocus({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("input,select,textarea")?.focus();
  }, []);
  return (
    <span ref={ref} style={{ display: "contents" }}>
      {children}
    </span>
  );
}

type TableGroup = { key: string; rows: EntityInstance[]; chip: React.ReactNode };

/** Split rows into ordered groups by a discrete field, for the collapsible table
 * sections (#GH-projects A). */
function groupRows(
  rows: EntityInstance[],
  field: string,
  type: EntityType | null,
  users: User[] | undefined,
  refIndex: RefIndex | undefined,
): TableGroup[] {
  const fs = roleOf(type, field);
  const byKey = new Map<string, EntityInstance[]>();
  const order: string[] = [];
  for (const e of rows) {
    const key = fieldText(e.fields[field]);
    let bucket = byKey.get(key);
    if (!bucket) {
      bucket = [];
      byKey.set(key, bucket);
      order.push(key);
    }
    bucket.push(e);
  }
  return order.map((value) => ({ key: value || "__none__", rows: byKey.get(value)!, chip: groupChip(value, fs, users, refIndex) }));
}

/** The header label for a group — a coloured status chip / actor name / ref title
 * / raw value; empty → "(none)". */
function groupChip(value: string, fs: EntityFieldSpec | undefined, users?: User[], refIndex?: RefIndex): React.ReactNode {
  if (!value) return <span className="ev-table__group-label ev-cell__empty">(none)</span>;
  if (fs?.role === "status") return <SelectChip value={value} fieldSpec={fs} />;
  if (fs?.role === "actor") return <span className="ev-table__group-label">{users?.find((u) => u.id === value)?.name ?? value}</span>;
  if (fs?.role === "ref" && fs.to && refIndex) {
    const t = refIndex.get(fs.to)?.get(Number(value));
    return <span className="ev-table__group-label">{t ? fieldText(t.fields.title) || `#${value}` : `#${value}?`}</span>;
  }
  return <span className="ev-table__group-label">{value}</span>;
}

/** A single-select value as a coloured chip (#GH-projects B). */
export function SelectChip({ value, fieldSpec }: { value: string; fieldSpec?: EntityFieldSpec }) {
  const c = selectColor(value, fieldSpec);
  return (
    <span className="ev-chip" style={{ background: c.bg, color: c.fg }}>
      {value}
    </span>
  );
}

/** The at-rest text for a value cell — the resolved actor name / referenced
 * record's title / `N%` progress / else the generic field text. A bare `ref`
 * column (e.g. `milestone`, not the `milestone.title` traversal) must still read
 * as the target's title, not the raw id — #1. */
function cellDisplay(
  fieldSpec: EntityFieldSpec | undefined,
  value: unknown,
  users?: User[],
  refOpts?: RefOption[],
): string {
  if (fieldSpec?.role === "actor") {
    const id = fieldText(value);
    return id ? (users?.find((u) => u.id === id)?.name ?? id) : "";
  }
  if (fieldSpec?.role === "ref") {
    const raw = fieldText(value);
    if (!raw) return "";
    const opt = refOpts?.find((o) => String(o.number) === raw);
    // resolved → the referenced title; dangling → "#N" (never the bare number).
    return opt ? opt.label : `#${raw}`;
  }
  if (fieldSpec?.role === "progress") {
    if (value == null || value === "") return "";
    return `${Math.max(0, Math.min(100, Number(value) || 0))}%`;
  }
  return fieldText(value);
}

/** Map each field with a lint warning to its message (for a yellow cell mark). */
function warningsByField(diagnostics: EntityInstance["diagnostics"]): Record<string, string> {
  const map: Record<string, string> = {};
  for (const d of diagnostics) {
    if (d.level === "warning" && d.field) map[d.field] = d.message;
  }
  return map;
}

/** Distinct non-empty display values of a column across the records. */
function distinct(entities: EntityInstance[], column: string): string[] {
  const seen = new Set<string>();
  for (const e of entities) {
    const v = fieldText(e.fields[column]);
    if (v) seen.add(v);
  }
  return [...seen];
}
