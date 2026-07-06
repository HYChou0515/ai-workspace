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

function FindingRow({ finding, onJump }: { finding: EntityHealthFinding; onJump?: (f: EntityHealthFinding) => void }) {
  const f = finding;
  const body = (
    <>
      <span className={`ev-level ev-level--${f.level === "error" ? "error" : "warning"}`}>{f.level}</span>
      <span className="ev-finding__loc">
        {f.type_name} #{f.number}
      </span>
      <span className="ev-finding__msg">
        {f.message}
        {f.field ? ` (${f.field})` : ""}
      </span>
    </>
  );
  // A finding is a jump button only when the container can open records; without
  // `onJump` it stays a plain row (no dead a11y button).
  if (onJump) {
    return (
      <button type="button" className="ev-finding" onClick={() => onJump(f)}>
        {body}
        <span className="ev-finding__jump" aria-hidden>
          →
        </span>
      </button>
    );
  }
  return <div className="ev-finding">{body}</div>;
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
    <div className="ev-panel">
      <div className="ev-panel__head">
        <h3 className="ev-panel__title">{title ?? "Health"}</h3>
      </div>
      {findings.length === 0 ? (
        <div className="ev-empty">
          <span className="ev-empty__icon" aria-hidden>
            ✓
          </span>
          <div className="ev-ok">All records are healthy — no findings.</div>
        </div>
      ) : (
        <>
          <div className="ev-health__summary">
            {errors > 0 && (
              <span className="ev-level ev-level--error">
                {errors} error{errors === 1 ? "" : "s"}
              </span>
            )}
            {warnings > 0 && (
              <span className="ev-level ev-level--warning">
                {warnings} warning{warnings === 1 ? "" : "s"}
              </span>
            )}
          </div>
          <div className="ev-health__filters">
            <label className="ev-health__filter">
              level{" "}
              <select className="ev-select" aria-label="filter level" value={level} onChange={(e) => setLevel(e.target.value)}>
                <option value="">all</option>
                <option value="error">error</option>
                <option value="warning">warning</option>
              </select>
            </label>
            <label className="ev-health__filter">
              type{" "}
              <select className="ev-select" aria-label="filter type" value={type} onChange={(e) => setType(e.target.value)}>
                <option value="">all</option>
                {types.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label className="ev-health__filter">
              field{" "}
              <select className="ev-select" aria-label="filter field" value={field} onChange={(e) => setField(e.target.value)}>
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
            <div className="ev-empty">
              <div>No findings match the current filters.</div>
            </div>
          ) : (
            <ul className="ev-health__list">
              {shown.map((f, i) => (
                <li key={`${f.type_name}-${f.number}-${i}`} className="ev-health__item">
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
