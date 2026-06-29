/**
 * Run progress for a workflow chat (#331). Collapsed it is a glanceable bar — status
 * chip, one segment per phase, a "step n · <title>" line, Stop, and an expand toggle.
 * Expanded it reveals the full #283 detail: phase diagram + metrics + a step-board /
 * timeline view toggle. Terminal clarity (#100) — the no-op banner, the human result
 * message, and the failures list — stays visible even while collapsed, so a finished
 * (or errored) run never re-buries *why* behind a fold.
 *
 * Presentational: the host (`ItemChatPanel`) owns the run query + cancel mutation and
 * feeds `run` / `disconnected` / `onStop`. The agent's reasoning + tool cards and live
 * step output already stream into the chat feed (`AgentEntryView`); this is the
 * structural overview above it — the inverse of #283's retired `WorkflowRunPanel`,
 * which is gone now that every App runs through the multi-chat `ItemChatShell` (#200).
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
import { usePersistentBoolean } from "../hooks/usePersistentBoolean";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { chipStyle } from "./StatusChip";
import { WorkflowPhaseDiagram } from "./WorkflowPhaseDiagram";
import { WorkflowStepBoard } from "./WorkflowStepBoard";
import { WorkflowTimeline } from "./WorkflowTimeline";

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

function statusLabel(t: ReturnType<typeof useT>, status: RunStatus): string {
  switch (status) {
    case "pending":
      return t("wf.status.pending");
    case "running":
      return t("wf.status.running");
    case "awaiting_human":
      return t("wf.status.awaiting_human");
    case "done":
      return t("wf.status.done");
    case "error":
      return t("wf.status.error");
    default: // cancelled
      return t("wf.status.cancelled");
  }
}

/** The color for one phase segment, keyed by its run status (mirrors the bar the
 * retired AgentPanel `ProgressBar` drew, so the glanceable look is unchanged). */
function phaseColor(status: string): string {
  if (status === "passed") return "var(--ok)";
  if (status === "running" || status === "awaiting_human") return "var(--accent)";
  if (status === "failed") return "var(--err)";
  return "var(--paper-3)"; // pending / skipped / unknown
}

/** A glanceable metrics strip (#283): elapsed, finished steps, retries. */
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

export function WorkflowProgress({
  run,
  declaredPhases,
  disconnected,
  onStop,
  stopping = false,
}: {
  run: WorkflowRunDTO | undefined;
  declaredPhases: PhaseDef[];
  /** #178: the run is live but the poll is failing → it may actually be dead. */
  disconnected: boolean;
  /** Stop the whole run (run-level cancel), not just the current turn. */
  onStop: () => void;
  stopping?: boolean;
}) {
  const t = useT();
  // Collapsed by default: most of the time the bar is enough; expand to drill in.
  const [expanded, setExpanded] = usePersistentBoolean("wf.progress.expanded", false);
  // #283: the step board stays the default; the timeline is the opt-in second view,
  // the choice remembered across runs/reloads (shared key with the old panel).
  const [timeline, setTimeline] = usePersistentBoolean("wf.view.timeline", false);

  if (!run) return null;

  const terminal = isRunTerminal(run.status);
  const nodes = phaseView(declaredPhases, run);
  // A run can finish `done` having executed nothing (e.g. a precondition no-op) —
  // flag it instead of letting a bare bar look identical to a fresh run (#100).
  const ranNothing =
    run.status === "done" && run.phases.every((p) => p.done === 0 && p.failed === 0);
  const message = typeof run.result?.message === "string" ? run.result.message : null;

  // The "current" segment for the collapsed summary: the phase the run is on, else
  // the one awaiting a human, else the first not-yet-passed, else the last.
  let currentIdx = nodes.findIndex((p) => p.current);
  if (currentIdx < 0) currentIdx = nodes.findIndex((p) => p.status === "awaiting_human");
  if (currentIdx < 0) currentIdx = nodes.findIndex((p) => p.status !== "passed");
  if (currentIdx < 0) currentIdx = nodes.length - 1;
  const current = currentIdx >= 0 ? nodes[currentIdx] : null;

  const showStop = !terminal && run.status !== "awaiting_human";

  return (
    <section
      data-testid="wf-progress"
      data-status={run.status}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "8px 12px",
        borderBottom: "1px solid var(--paper-3)",
      }}
    >
      {/* Always-visible bar: status + phase segments + Stop + expand toggle. */}
      <div data-testid="wf-progress-bar" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span data-testid="wf-run-status" style={chipStyle(runTone(run.status))}>
          {statusLabel(t, run.status)}
        </span>
        {nodes.length > 0 && (
          <div style={{ display: "flex", gap: 4, flex: 1 }}>
            {nodes.map((p) => (
              <div
                key={p.id}
                title={p.title}
                style={{ flex: 1, height: 4, borderRadius: 2, background: phaseColor(p.status) }}
              />
            ))}
          </div>
        )}
        {showStop && (
          <button
            type="button"
            data-testid="wf-stop"
            onClick={onStop}
            disabled={stopping}
            style={{
              padding: "2px 10px",
              borderRadius: 6,
              border: "1px solid var(--err)",
              background: "transparent",
              color: "var(--err)",
              cursor: stopping ? "default" : "pointer",
              opacity: stopping ? 0.6 : 1,
              fontSize: pxToRem(11.5),
            }}
          >
            {t("wf.stop")}
          </button>
        )}
        <button
          type="button"
          data-testid="wf-progress-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
          style={{
            padding: "2px 8px",
            borderRadius: 6,
            border: "1px solid var(--paper-3)",
            background: "transparent",
            color: "var(--text-paper-d)",
            cursor: "pointer",
            fontSize: pxToRem(11.5),
            whiteSpace: "nowrap",
          }}
        >
          {expanded ? t("wf.progress.collapse") : t("wf.progress.expand")}
        </button>
      </div>

      {current && (
        <div data-testid="wf-progress-summary" style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
          {t("wf.progress.step", { n: currentIdx + 1, title: current.title })}
        </div>
      )}

      {/* Never-buried clarity (#100): health + terminal outcome show even collapsed. */}
      {disconnected && !terminal && (
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
          {t("wf.disconnected")}
        </div>
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
          {t("wf.progress.noop")}
          {message ? `：${message}` : "。"}
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

      {run.failures.length > 0 && (
        <ul data-testid="wf-failures" style={{ margin: 0, paddingLeft: 18, fontSize: pxToRem(12) }}>
          {run.failures.map((f) => (
            <li key={f.key} style={{ color: "var(--err)" }}>
              <code>{f.key}</code>: {f.error}
            </li>
          ))}
        </ul>
      )}

      {/* Expandable #283 detail. */}
      {expanded && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 2 }}>
          <WorkflowPhaseDiagram nodes={nodes} />
          <WorkflowMetrics run={run} />
          <div
            role="tablist"
            aria-label="run view"
            style={{
              display: "flex",
              gap: 0,
              alignSelf: "flex-start",
              borderRadius: 6,
              overflow: "hidden",
              border: "1px solid var(--paper-3)",
            }}
          >
            <ViewTab active={!timeline} onClick={() => setTimeline(false)} testid="wf-view-steps">
              {t("wf.view.steps")}
            </ViewTab>
            <ViewTab active={timeline} onClick={() => setTimeline(true)} testid="wf-view-timeline">
              {t("wf.view.timeline")}
            </ViewTab>
          </div>
          {timeline ? (
            <WorkflowTimeline steps={run.steps} live={!terminal} />
          ) : (
            <WorkflowStepBoard nodes={nodes} steps={run.steps} />
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
        </div>
      )}
    </section>
  );
}
