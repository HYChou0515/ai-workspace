/**
 * project-health view (#419 §E3) — a cross-type list of parser/lint findings.
 * Not entity-bound, so it lives outside `EntityViewBody` / the `viewKindRegistry`
 * (which key on one entity type); the container feeds it the health endpoint's
 * findings directly. Deeper filtering + jump-to-entity land in the F follow-up.
 */

import type { EntityHealthFinding } from "../../api/entities";
import { pxToRem } from "../../lib/pxToRem";

export function HealthView({ title, findings }: { title?: string; findings: EntityHealthFinding[] }) {
  const errors = findings.filter((f) => f.level === "error").length;
  const warnings = findings.length - errors;
  return (
    <div style={{ padding: 12 }}>
      <h3 style={{ margin: "0 0 10px" }}>{title ?? "Health"}</h3>
      {findings.length === 0 ? (
        <div style={{ color: "var(--ok)" }}>All records are healthy — no findings.</div>
      ) : (
        <>
          <div style={{ marginBottom: 8, fontSize: pxToRem(13), color: "var(--text-paper-d)" }}>
            {errors} error{errors === 1 ? "" : "s"}, {warnings} warning{warnings === 1 ? "" : "s"}
          </div>
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {findings.map((f, i) => (
              <li
                key={`${f.type_name}-${f.number}-${i}`}
                style={{ display: "flex", gap: 8, padding: "4px 0", borderBottom: "1px solid var(--paper-3)" }}
              >
                <span
                  style={{
                    color: f.level === "error" ? "var(--err)" : "var(--warn)",
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
