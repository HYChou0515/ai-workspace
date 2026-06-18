/**
 * Review decision card (#100, manual §10) — the most important human-facing moment.
 * Shown when a run is `awaiting_human`: a title, the thing-to-review (a routing plan
 * or a generated summary), and the gate's allowed actions. "revise" reveals a
 * free-text input. Approving commits; rejecting ends the run for takeover.
 *
 * Prop-driven: the parent passes the `PendingDecision` + an `onDecide` that posts to
 * the decisions endpoint, so this is a pure form.
 */

import { useState } from "react";

import type { PendingDecision } from "../api/workflows";

const ACTION_LABEL: Record<string, string> = {
  approve: "Approve",
  reject: "Reject",
  revise: "Revise",
};

export function WorkflowDecisionCard({
  decision,
  onDecide,
  busy,
}: {
  decision: PendingDecision;
  onDecide: (choice: string, input?: string) => void;
  busy?: boolean;
}) {
  const [revising, setRevising] = useState(false);
  const [note, setNote] = useState("");
  const allow = decision.allow.length ? decision.allow : ["approve", "reject"];

  return (
    <section
      data-testid="wf-decision-card"
      style={{
        border: "1px solid var(--warn)",
        borderRadius: "var(--radius-card, 8px)",
        background: "rgba(198,138,46,.08)",
        padding: 12,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <header style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontWeight: 600 }}>{decision.title || "Awaiting your decision"}</span>
        <span style={{ fontSize: 11, color: "var(--warn)", fontFamily: "var(--font-mono)" }}>
          awaiting you
        </span>
      </header>
      {decision.summary && (
        <pre
          data-testid="wf-decision-summary"
          style={{
            margin: 0,
            maxHeight: 220,
            overflow: "auto",
            background: "var(--paper-2)",
            borderRadius: 6,
            padding: 8,
            fontSize: 12,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {decision.summary}
        </pre>
      )}
      {revising && (
        <textarea
          data-testid="wf-revise-input"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="What should change?"
          rows={3}
          style={{ width: "100%", fontFamily: "inherit", fontSize: 12 }}
        />
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {allow.map((choice) => {
          const isRevise = choice === "revise";
          const onClick = () => {
            if (isRevise && !revising) {
              setRevising(true);
              return;
            }
            onDecide(choice, isRevise ? note : undefined);
          };
          return (
            <button
              key={choice}
              type="button"
              data-action={choice}
              disabled={busy}
              onClick={onClick}
              style={{
                padding: "5px 12px",
                borderRadius: 6,
                border: "1px solid var(--line)",
                cursor: busy ? "default" : "pointer",
                background:
                  choice === "approve" ? "var(--ok)" : choice === "reject" ? "var(--err)" : "var(--paper-2)",
                color: choice === "approve" || choice === "reject" ? "#fff" : "var(--text-paper)",
                fontWeight: 500,
              }}
            >
              {ACTION_LABEL[choice] ?? choice}
            </button>
          );
        })}
      </div>
    </section>
  );
}
