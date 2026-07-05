/**
 * gantt view (#419 §B) — records laid out as bars on a shared timeline derived
 * from a `daterange` field; records without a parseable span are dropped from
 * the chart. Read-only in P1 (drag-to-reschedule + dependency lines land in the
 * A2 follow-up). Registered as the `gantt` kind in `viewKindRegistry`.
 */

import type { EntityInstance } from "../../api/entities";
import { fieldText, parseSpan } from "./shared";
import type { EntityViewProps } from "./types";

export function GanttView({ spec, entities }: EntityViewProps) {
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
          <div style={{ position: "relative", flex: 1, height: 18, background: "var(--paper-2)", borderRadius: 4 }}>
            <div
              data-testid={`bar-${e.number}`}
              title={fieldText(e.fields[spanField])}
              style={{
                position: "absolute",
                left: `${scale(span.start)}%`,
                width: `${Math.max(scale(span.end) - scale(span.start), 1)}%`,
                top: 0,
                bottom: 0,
                background: "var(--accent)",
                borderRadius: 4,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
