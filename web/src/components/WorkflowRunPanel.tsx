/**
 * Run progress view (#100, manual §12 — the centerpiece): one run's live status +
 * phase diagram + Stop +, when paused, the review decision card. Glanceable ("where
 * are we / what broke") and driven by `useRun` (polls while live). The agent's
 * reasoning / tool cards are NOT re-rendered here — a run is a turn on the item, so
 * they already appear in the normal chat (AgentEntryView); this is the run header.
 */

import type { ChipTone } from "../api/types";
import { isRunTerminal, phaseView, type PhaseDef, type RunStatus } from "../api/workflows";
import { useCancelRun, useDecide, useRun } from "../hooks/useWorkflow";
import { chipStyle } from "./StatusChip";
import { WorkflowDecisionCard } from "./WorkflowDecisionCard";
import { WorkflowPhaseDiagram } from "./WorkflowPhaseDiagram";

function runTone(status: RunStatus): ChipTone {
  switch (status) {
    case "done":
      return "ok";
    case "error":
      return "err";
    case "running":
      return "info";
    case "awaiting_human":
      return "warn";
    default: // pending | cancelled
      return "muted";
  }
}

const STATUS_LABEL: Record<RunStatus, string> = {
  pending: "queued",
  running: "running",
  awaiting_human: "awaiting you",
  done: "done",
  error: "failed",
  cancelled: "cancelled",
};

export function WorkflowRunPanel({
  slug,
  itemId,
  runId,
  declaredPhases,
}: {
  slug: string;
  itemId: string;
  runId: string;
  declaredPhases: PhaseDef[];
}) {
  const { data: run } = useRun(slug, itemId, runId);
  const cancel = useCancelRun(slug, itemId);
  const decide = useDecide(slug, itemId, runId);

  if (!run) return <div data-testid="wf-run-loading">Loading run…</div>;

  const terminal = isRunTerminal(run.status);
  const nodes = phaseView(declaredPhases, run);
  // #100 observability: a run can finish `done` having executed nothing (e.g. a
  // precondition no-op). That used to look identical to a fresh/idle run. Flag it,
  // and surface the workflow's human-readable reason instead of a raw status token.
  const ranNothing =
    run.status === "done" && run.phases.every((p) => p.done === 0 && p.failed === 0);
  const message = typeof run.result?.message === "string" ? run.result.message : null;

  return (
    <section
      data-testid="wf-run-panel"
      data-status={run.status}
      style={{ display: "flex", flexDirection: "column", gap: 10 }}
    >
      <header style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span data-testid="wf-run-status" style={chipStyle(runTone(run.status))}>
          {STATUS_LABEL[run.status]}
        </span>
        {!terminal && run.status !== "awaiting_human" && (
          <button
            type="button"
            data-testid="wf-stop"
            onClick={() => cancel.mutate(runId)}
            disabled={cancel.isPending}
            style={{
              marginLeft: "auto",
              padding: "3px 10px",
              borderRadius: 6,
              border: "1px solid var(--err)",
              background: "transparent",
              color: "var(--err)",
              cursor: "pointer",
            }}
          >
            Stop
          </button>
        )}
      </header>

      <WorkflowPhaseDiagram nodes={nodes} />

      {run.status === "awaiting_human" && run.pending_decision && (
        <WorkflowDecisionCard
          decision={run.pending_decision}
          busy={decide.isPending}
          onDecide={(choice, input) => decide.mutate({ choice, input })}
        />
      )}

      {ranNothing && (
        <div
          data-testid="wf-noop"
          style={{
            padding: "8px 10px",
            background: "var(--paper-2)",
            borderLeft: "2px solid var(--text-paper-d2)",
            borderRadius: 6,
            fontSize: 12,
            color: "var(--text-paper)",
          }}
        >
          已完成，但未執行任何步驟{message ? `：${message}` : "。"}
        </div>
      )}

      {!ranNothing && terminal && message && (
        <div
          data-testid="wf-run-message"
          style={{
            padding: "8px 10px",
            background: "var(--paper-2)",
            borderRadius: 6,
            fontSize: 12,
            color: "var(--text-paper)",
          }}
        >
          {message}
        </div>
      )}

      {!ranNothing && terminal && run.result && !message && (
        <pre
          data-testid="wf-run-result"
          style={{
            margin: 0,
            background: "var(--paper-2)",
            borderRadius: 6,
            padding: 8,
            fontSize: 12,
            whiteSpace: "pre-wrap",
          }}
        >
          {JSON.stringify(run.result, null, 2)}
        </pre>
      )}

      {run.failures.length > 0 && (
        <ul data-testid="wf-failures" style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
          {run.failures.map((f) => (
            <li key={f.key} style={{ color: "var(--err)" }}>
              <code>{f.key}</code>: {f.error}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
