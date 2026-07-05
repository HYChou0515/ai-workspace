/**
 * project-health view (#419 §E3, #448 F / #454) — a cross-type list of the
 * parser/lint findings the layered degradation (§D) collects. Not entity-bound,
 * so it lives outside `EntityViewBody` / the `viewKindRegistry`; the container
 * feeds it the health endpoint's findings directly.
 *
 * #454 deepens it into a queryable panel: filter by level / entity type / field,
 * and click a finding to jump to the offending record (the container wires
 * `onJump` to open its file). This is the exit of the "warning-not-death"
 * philosophy — broken things don't kill the app, they collect here to be fixed.
 */

import { useMemo, useState } from "react";

import type { EntityHealthFinding } from "../../api/entities";
import { pxToRem } from "../../lib/pxToRem";

const rowStyle: React.CSSProperties = { display: "flex", gap: 8, padding: "4px 0", width: "100%", textAlign: "left" };

function FindingRow({ finding, onJump }: { finding: EntityHealthFinding; onJump?: (f: EntityHealthFinding) => void }) {
  const f = finding;
  const body = (
    <>
      <span style={{ color: f.level === "error" ? "var(--err)" : "var(--warn)", fontWeight: 600, minWidth: 64 }}>
        {f.level}
      </span>
      <span style={{ minWidth: 90 }}>
        {f.type_name} #{f.number}
      </span>
      <span>
        {f.message}
        {f.field ? ` (${f.field})` : ""}
      </span>
    </>
  );
  // A finding is a jump button only when the container can open records; without
  // `onJump` it stays a plain row (no dead a11y button).
  if (onJump) {
    return (
      <button
        type="button"
        onClick={() => onJump(f)}
        style={{ ...rowStyle, background: "none", border: "none", cursor: "pointer", font: "inherit", color: "inherit" }}
      >
        {body}
      </button>
    );
  }
  return <div style={rowStyle}>{body}</div>;
}

export function HealthView({
  title,
  findings,
  onJump,
}: {
  title?: string;
  findings: EntityHealthFinding[];
  onJump?: (finding: EntityHealthFinding) => void;
}) {
  const [level, setLevel] = useState("");
  const [type, setType] = useState("");
  const [field, setField] = useState("");

  const types = useMemo(() => [...new Set(findings.map((f) => f.type_name))], [findings]);
  const fields = useMemo(
    () => [...new Set(findings.map((f) => f.field).filter((x): x is string => !!x))],
    [findings],
  );

  const errors = findings.filter((f) => f.level === "error").length;
  const warnings = findings.length - errors;

  const shown = findings.filter(
    (f) =>
      (level === "" || f.level === level) &&
      (type === "" || f.type_name === type) &&
      (field === "" || f.field === field),
  );

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
          <div style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap", fontSize: pxToRem(13) }}>
            <label>
              level{" "}
              <select aria-label="filter level" value={level} onChange={(e) => setLevel(e.target.value)}>
                <option value="">all</option>
                <option value="error">error</option>
                <option value="warning">warning</option>
              </select>
            </label>
            <label>
              type{" "}
              <select aria-label="filter type" value={type} onChange={(e) => setType(e.target.value)}>
                <option value="">all</option>
                {types.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label>
              field{" "}
              <select aria-label="filter field" value={field} onChange={(e) => setField(e.target.value)}>
                <option value="">all</option>
                {fields.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {shown.length === 0 ? (
            <div style={{ color: "var(--text-paper-d)" }}>No findings match the current filters.</div>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {shown.map((f, i) => (
                <li key={`${f.type_name}-${f.number}-${i}`} style={{ borderBottom: "1px solid var(--paper-3)" }}>
                  <FindingRow finding={f} onJump={onJump} />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
