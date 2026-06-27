/**
 * Run progress view (#100, manual §12 — the centerpiece): one run's live status +
 * phase diagram + Stop +, when paused, the review decision card. Glanceable ("where
 * are we / what broke") and driven by `useRun` (polls while live). The agent's
 * reasoning / tool cards are NOT re-rendered here — a run is a turn on the item, so
 * they already appear in the normal chat (AgentEntryView); this is the run header.
 */

import type { ChipTone } from "../api/types";
import {
  fmtElapsed,
  isRunTerminal,
  phaseView,
  type PhaseDef,
  type RunStatus,
  type WorkflowRunDTO,
} from "../api/workflows";
import { useCancelRun, useDecide, useRun } from "../hooks/useWorkflow";
import { usePersistentBoolean } from "../hooks/usePersistentBoolean";
import { useT } from "../lib/i18n";
import { chipStyle } from "./StatusChip";
import { WorkflowDecisionCard } from "./WorkflowDecisionCard";
import { WorkflowPhaseDiagram } from "./WorkflowPhaseDiagram";
import { WorkflowStepBoard } from "./WorkflowStepBoard";
import { WorkflowTimeline } from "./WorkflowTimeline";
import { pxToRem } from "../lib/pxToRem";

/** A glanceable metrics strip above the views (#283): how long the run has taken, how
 * many steps finished, and how many retries it cost. View-independent. */
function WorkflowMetrics({ run }: { run: WorkflowRunDTO }) {
  const t = useT();
  if (run.started == null) return null;
  const elapsed = Math.max(0, (run.ended ?? Date.now()) - run.started);
  const done = run.phases.reduce((n, p) => n + p.done, 0);
  const failed = run.phases.reduce((n, p) => n + p.failed, 0);
  const retries = run.steps.reduce((n, s) => n + s.attempts, 0);
  return (
    <div
      data-testid="wf-metrics"
      style={{ display: "flex", gap: 10, flexWrap: "wrap", fontSize: pxToRem(11.5), color: "var(--text-paper-d)" }}
    >
      <span>
        {t("wf.metrics.elapsed")} {fmtElapsed(elapsed)}
      </span>
      <span>
        {done} {t("wf.metrics.steps")}
        {failed > 0 ? ` · ✗${failed}` : ""}
      </span>
      {retries > 0 && <span>{t("wf.metrics.retries", { n: retries })}</span>}
    </div>
  );
}

function ViewTab({
  active,
  onClick,
  testid,
  children,
}: {
  active: boolean;
  onClick: () => void;
  testid: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      data-testid={testid}
      onClick={onClick}
      style={{
        padding: "3px 12px",
        border: "none",
        background: active ? "var(--accent, var(--info))" : "var(--white)",
        color: active ? "#fff" : "var(--text-paper-d)",
        cursor: "pointer",
        fontSize: pxToRem(11.5),
        fontWeight: active ? 600 : 400,
      }}
    >
      {children}
    </button>
  );
}

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
  const t = useT();
  const runQuery = useRun(slug, itemId, runId);
  const run = runQuery.data;
  const cancel = useCancelRun(slug, itemId);
  const decide = useDecide(slug, itemId, runId);
  // #283: the simple step board stays the default; the timeline is the opt-in second
  // view, the choice remembered across runs/reloads.
  const [timeline, setTimeline] = usePersistentBoolean("wf.view.timeline", false);

  if (!run) return <div data-testid="wf-run-loading">Loading run…</div>;

  const terminal = isRunTerminal(run.status);
  // #178 silent-step liveness backstop: while a run is live, the panel polls; if
  // that poll is failing the backend is unreachable, so a wedged/silent step might
  // actually be dead. Surface it instead of letting the board look frozen-but-fine.
  const disconnected = (runQuery.failureCount ?? 0) > 0 && !terminal;
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

      {disconnected && (
        <div
          data-testid="wf-disconnected"
          style={{
            padding: "8px 10px",
            background: "var(--paper-2)",
            borderLeft: "2px solid var(--err)",
            borderRadius: 6,
            fontSize: pxToRem(12),
            color: "var(--err)",
          }}
        >
          連線中斷，可能已停止。正在嘗試重新連線…
        </div>
      )}

      <WorkflowPhaseDiagram nodes={nodes} />

      {/* #283: run metrics + a step-board / timeline view toggle, both always above
          whichever view is showing. */}
      <WorkflowMetrics run={run} />
      <div
        role="tablist"
        aria-label="run view"
        style={{ display: "flex", gap: 0, alignSelf: "flex-start", borderRadius: 6, overflow: "hidden", border: "1px solid var(--paper-3)" }}
      >
        <ViewTab active={!timeline} onClick={() => setTimeline(false)} testid="wf-view-steps">
          {t("wf.view.steps")}
        </ViewTab>
        <ViewTab active={timeline} onClick={() => setTimeline(true)} testid="wf-view-timeline">
          {t("wf.view.timeline")}
        </ViewTab>
      </div>

      {/* #178 step board (default) / #283 timeline — which step ran when + for how long,
          so a long deterministic step or a gate wait doesn't look dead. Poll-driven. */}
      {timeline ? (
        <WorkflowTimeline steps={run.steps} live={!terminal} />
      ) : (
        <WorkflowStepBoard nodes={nodes} steps={run.steps} />
      )}

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
            fontSize: pxToRem(12),
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
            fontSize: pxToRem(12),
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
            fontSize: pxToRem(12),
            whiteSpace: "pre-wrap",
          }}
        >
          {JSON.stringify(run.result, null, 2)}
        </pre>
      )}

      {run.failures.length > 0 && (
        <ul data-testid="wf-failures" style={{ margin: 0, paddingLeft: 18, fontSize: pxToRem(12) }}>
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
