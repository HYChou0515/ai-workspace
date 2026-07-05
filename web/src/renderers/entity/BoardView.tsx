/**
 * board view (#419 §B) — records grouped into columns by a `status` field; a
 * card's status select moves it between columns (the update write path). Empty
 * columns from the field's closed vocabulary still render. Registered as the
 * `board` kind in `viewKindRegistry`.
 */

import type { EntityInstance } from "../../api/entities";
import { pxToRem } from "../../lib/pxToRem";
import { EditableCell, fieldText, roleOf } from "./shared";
import type { EntityViewProps } from "./types";

const UNSET = " unset";

function distinctValues(entities: EntityInstance[], field: string): string[] {
  const seen = new Set<string>();
  for (const e of entities) {
    const v = fieldText(e.fields[field]);
    if (v) seen.add(v);
  }
  return [...seen];
}

export function BoardView({ spec, type, entities, onPatch, busy }: EntityViewProps) {
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
              <div key={e.number} style={{ border: "1px solid var(--paper-3)", borderRadius: 6, padding: 8, marginBottom: 8 }}>
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
