/**
 * Steer confirm card (#288, manual §10) — the review step of conversational steering.
 * Shown when a run has a `pending_steer`: the operator's instruction, the steerer's
 * rationale, and the **blast radius** (which input files change + which steps will
 * re-run vs. be reused) so the cost is visible before it applies. Approve applies the
 * edits + resumes the run; Reject discards the plan.
 *
 * Prop-driven (like {@link WorkflowDecisionCard}): the parent passes the `SteerPlan` +
 * an `onConfirm(approve)` that posts to the steer/confirm endpoint, so this is a pure
 * form. To change the plan, the operator types a new instruction (re-steer) — there is
 * no inline editing here.
 */

import type { SteerPlan } from "../api/workflows";
import { Icon } from "./Icon";
import { pxToRem } from "../lib/pxToRem";

export function SteerConfirmCard({
  plan,
  onConfirm,
  busy,
}: {
  plan: SteerPlan;
  onConfirm: (approve: boolean) => void;
  busy?: boolean;
}) {
  return (
    <section
      data-testid="wf-steer-card"
      style={{
        border: "1px solid var(--info)",
        borderRadius: "var(--radius-card, 8px)",
        background: "rgba(46,118,198,.08)",
        padding: 12,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <header style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            padding: "2px 8px",
            borderRadius: 999,
            background: "var(--info)",
            color: "#fff",
            fontSize: pxToRem(11),
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: ".04em",
          }}
        >
          <Icon name="bell" size={11} color="#fff" />
          Review steer
        </span>
        <span style={{ fontWeight: 600 }} data-testid="wf-steer-instruction">
          {plan.instruction}
        </span>
      </header>

      {plan.rationale && (
        <p style={{ margin: 0, fontSize: pxToRem(12.5), color: "var(--text-paper)" }}>
          {plan.rationale}
        </p>
      )}

      {plan.input_edits.length > 0 && (
        <div data-testid="wf-steer-edits" style={{ fontSize: pxToRem(12) }}>
          <div style={{ color: "var(--text-paper-d)", marginBottom: 2 }}>Inputs to change</div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {plan.input_edits.map((e) => (
              <li key={e.path}>
                <code>{e.path}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {plan.invalidate.length > 0 && (
        <div data-testid="wf-steer-invalidate" style={{ fontSize: pxToRem(12) }}>
          <div style={{ color: "var(--text-paper-d)", marginBottom: 2 }}>
            Steps to re-run (others are reused)
          </div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {plan.invalidate.map((name) => (
              <li key={name}>
                <code>{name}</code>
              </li>
            ))}
          </ul>
          <div style={{ color: "var(--text-paper-d)", marginTop: 2, fontStyle: "italic" }}>
            …and anything downstream of these.
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button
          type="button"
          data-testid="wf-steer-approve"
          disabled={busy}
          onClick={() => onConfirm(true)}
          style={{
            padding: "5px 12px",
            borderRadius: 6,
            border: "1px solid var(--paper-3)",
            cursor: busy ? "default" : "pointer",
            background: "var(--ok)",
            color: "#fff",
            fontWeight: 500,
          }}
        >
          Apply &amp; resume
        </button>
        <button
          type="button"
          data-testid="wf-steer-reject"
          disabled={busy}
          onClick={() => onConfirm(false)}
          style={{
            padding: "5px 12px",
            borderRadius: 6,
            border: "1px solid var(--paper-3)",
            cursor: busy ? "default" : "pointer",
            background: "var(--paper-2)",
            color: "var(--text-paper)",
            fontWeight: 500,
          }}
        >
          Discard
        </button>
      </div>
    </section>
  );
}
