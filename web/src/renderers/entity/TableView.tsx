/**
 * table view (#419 §B, #448 P5) — every record in a grid; status / progress /
 * scalar cells edit inline through the update write path. Column set comes from
 * the view spec (`columns`), else the schema's fields, else the union of record
 * keys. Sorting (header click), value filtering (status / actor domains), and
 * column show/hide are all local + ephemeral to the open panel ("本地即可").
 * Registered as the `table` kind in `viewKindRegistry`.
 */

import { useState } from "react";

import type { EntityFieldSpec, EntityInstance, EntityType } from "../../api/entities";
import { pxToRem } from "../../lib/pxToRem";
import { refOptions, traverseColumn } from "./refTraversal";
import { RoleField, widgetForRole } from "./roleWidget";
import { fieldText, roleOf } from "./shared";
import { filterEntities, sortEntities, type SortDir } from "./tableOps";
import type { EntityViewProps, ViewSpec } from "./types";

function columnsFor(spec: ViewSpec, type: EntityType | null, entities: EntityInstance[]): string[] {
  if (spec.columns && spec.columns.length > 0) return spec.columns;
  if (type) return type.fields.map((f) => f.name);
  // No schema + no explicit columns → union of the records' own keys.
  const seen = new Set<string>();
  for (const e of entities) for (const k of Object.keys(e.fields)) seen.add(k);
  return [...seen];
}

const cellStyle: React.CSSProperties = {
  border: "1px solid var(--paper-3)",
  padding: "4px 8px",
  textAlign: "left",
  verticalAlign: "top",
};

const headerBtnStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  padding: 0,
  font: "inherit",
  fontWeight: 600,
  cursor: "pointer",
  color: "inherit",
};

type FilterOption = { value: string; label: string };

export function TableView({ spec, type, entities, invalid, users, refIndex, onPatch, busy }: EntityViewProps) {
  const allColumns = columnsFor(spec, type, entities);
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

  return (
    <div>
      <div style={{ position: "relative", marginBottom: 8 }}>
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
          <div
            role="menu"
            style={{
              position: "absolute",
              zIndex: 1,
              marginTop: 4,
              padding: 8,
              background: "var(--paper)",
              border: "1px solid var(--paper-3)",
              borderRadius: 6,
            }}
          >
            {allColumns.map((c) => (
              <label key={c} style={{ display: "block", whiteSpace: "nowrap" }}>
                <input type="checkbox" aria-label={`toggle ${c}`} checked={!hidden.has(c)} onChange={() => toggleColumn(c)} /> {c}
              </label>
            ))}
          </div>
        )}
      </div>

      {selected.size > 0 && (
        <div
          role="toolbar"
          aria-label="batch actions"
          style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}
        >
          <span style={{ fontSize: pxToRem(13), color: "var(--text-paper-d)" }}>{selected.size} selected</span>
          {batchFields.map((f) => (
            <label key={f.name} style={{ fontSize: pxToRem(13) }}>
              {f.name}:{" "}
              <select
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

      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr>
            <th style={cellStyle}>
              <input type="checkbox" aria-label="select all" checked={allSelected} onChange={toggleAll} />
            </th>
            <th style={cellStyle}>#</th>
            {columns.map((c) => (
              <th key={c} style={cellStyle}>
                <button type="button" style={headerBtnStyle} onClick={() => cycleSort(c)}>
                  {c}
                  {sort?.column === c ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}
                </button>
              </th>
            ))}
          </tr>
          {hasFilters && (
            <tr>
              <th style={cellStyle} />
              <th style={cellStyle} />
              {columns.map((c) => {
                const domain = filterDomain(c);
                return (
                  <th key={c} style={cellStyle}>
                    {domain && (
                      <select
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
          {rows.map((e) => {
            // A lint warning marks its field's cell yellow, still editable (§D).
            const warn = warningsByField(e.diagnostics);
            return (
              <tr key={e.number}>
                <td style={cellStyle}>
                  <input
                    type="checkbox"
                    aria-label={`select ${e.number}`}
                    checked={selected.has(e.number)}
                    onChange={() => toggleRow(e.number)}
                  />
                </td>
                <td style={cellStyle}>{e.number}</td>
                {columns.map((c) => {
                  const warnMsg = warn[c];
                  const td = warnMsg
                    ? { style: { ...cellStyle, borderLeft: "3px solid var(--warn)" }, title: warnMsg }
                    : { style: cellStyle };
                  // A dotted `milestone.title` column follows the ref at render time
                  // (§A4); a dangling target degrades to a marker, never a crash (§D).
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
                      <RoleField
                        widget={fieldSpec ? widgetForRole(fieldSpec.role) : "readonly"}
                        name={fieldSpec?.name ?? c}
                        value={e.fields[c]}
                        values={fieldSpec?.values}
                        users={users}
                        refOptions={opts}
                        disabled={busy}
                        onCommit={(next) => onPatch(e.number, { [c]: next })}
                      />
                    </td>
                  );
                })}
              </tr>
            );
          })}
          {/* Unparseable records degrade to an error row (§D) — never dropped
              silently; the raw body shows so the fix is visible. */}
          {(invalid ?? []).map((e) => (
            <tr key={`invalid-${e.number}`}>
              <td style={cellStyle} />
              <td style={{ ...cellStyle, color: "var(--err)" }}>#{e.number}</td>
              <td colSpan={Math.max(columns.length, 1)} style={{ ...cellStyle, color: "var(--err)" }}>
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
  );
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
