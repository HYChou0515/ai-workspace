/**
 * table view (#419 §B) — every record in a grid; status / progress / scalar
 * cells edit inline through the update write path. Column set comes from the
 * view spec (`columns`), else the schema's fields, else the union of record
 * keys. Registered as the `table` kind in `viewKindRegistry`.
 */

import type { EntityInstance, EntityType } from "../../api/entities";
import { refOptions, traverseColumn } from "./refTraversal";
import { RoleField, widgetForRole } from "./roleWidget";
import { roleOf } from "./shared";
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

export function TableView({ spec, type, entities, users, refIndex, onPatch, busy }: EntityViewProps) {
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
            {columns.map((c) => {
              // A dotted `milestone.title` column follows the ref at render time
              // (§A4); a dangling target degrades to a marker, never a crash (§D).
              const traversal = refIndex ? traverseColumn(c, e, type, refIndex) : null;
              if (traversal) {
                return (
                  <td key={c} style={cellStyle}>
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
                <td key={c} style={cellStyle}>
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
        ))}
      </tbody>
    </table>
  );
}
