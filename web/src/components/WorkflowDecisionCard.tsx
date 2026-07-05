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

import { useT } from "../lib/i18n";
import type { PendingDecision } from "../api/workflows";
import { Icon } from "./Icon";
import { pxToRem } from "../lib/pxToRem";

const ACTION_LABEL: Record<string, string> = {
  approve: "Approve",
  reject: "Reject",
  revise: "Revise",
};

export function WorkflowDecisionCard({
  decision,
  onDecide,
  busy,
  aux,
}: {
  decision: PendingDecision;
  onDecide: (choice: string, input?: string) => void;
  busy?: boolean;
  // Extra action rendered alongside the decision buttons (#205: the "View changes"
  // button that opens the context-card diff). Generic so RCA gates pass nothing.
  aux?: React.ReactNode;
}) {
  const t = useT();
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
      <header style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        {/* A loud, pill-shaped cue (#170): the old 11px grey "awaiting you" read
            as a status note, not "it's your turn to act". */}
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            padding: "2px 8px",
            borderRadius: 999,
            background: "var(--warn)",
            color: "var(--text-dark)",
            fontSize: pxToRem(11),
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: ".04em",
          }}
        >
          <Icon name="bell" size={11} color="#fff" />
          {t("wf.decision.cue")}
        </span>
        <span style={{ fontWeight: 600 }}>{decision.title || t("wf.decision.titleFallback")}</span>
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
            fontSize: pxToRem(12),
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
          style={{ width: "100%", fontFamily: "inherit", fontSize: pxToRem(12) }}
        />
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {aux}
        {allow.map((choice) => {
          const isRevise = choice === "revise";
          const onClick = () => {
            if (isRevise && !revising) {
              setRevising(true);
              return;
            }
            onDecide(choice, isRevise ? note : undefined);
          };
          const variant = choice === "approve" ? "primary" : choice === "reject" ? "danger" : "secondary";
          return (
            <button
              key={choice}
              type="button"
              className="btn"
              data-variant={variant}
              data-size="sm"
              data-action={choice}
              disabled={busy}
              onClick={onClick}
            >
              {ACTION_LABEL[choice] ?? choice}
            </button>
          );
        })}
      </div>
    </section>
  );
}
