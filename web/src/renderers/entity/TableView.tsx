/**
 * table view (#419 §B) — every record in a grid; status / progress / scalar
 * cells edit inline through the update write path. Column set comes from the
 * view spec (`columns`), else the schema's fields, else the union of record
 * keys. Registered as the `table` kind in `viewKindRegistry`.
 */

import type { EntityInstance, EntityType } from "../../api/entities";
import { EditableCell, roleOf } from "./shared";
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

export function TableView({ spec, type, entities, onPatch, busy }: EntityViewProps) {
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
