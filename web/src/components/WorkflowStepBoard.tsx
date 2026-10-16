/**
 * Per-step status board (#178) — the drill-down under the phase diagram so you can
 * see, for each step, whether it's running or dead and for how long. Driven by the
 * run's persisted `steps` (poll), so it survives a reload and shows a server-side
 * elapsed ("running 4:32") even when a step is wedged. Loop elements are collapsed
 * server-side into the phase `done / total` counter, so this stays bounded.
 *
 * Live stdout from a deterministic step streams into the chat feed (the SSE-driven
 * `AgentEntryView`), not here — this board is the reload-safe status home.
 */

import { useEffect, useState } from "react";

import type { ChipTone } from "../api/types";
import { useT } from "../lib/i18n";
import {
  fmtElapsed,
  stepBoard,
  stepElapsedMs,
  type PhaseNode,
  type StepStateDTO,
} from "../api/workflows";
import { chipStyle } from "./StatusChip";
import { pxToRem } from "../lib/pxToRem";

function stepTone(status: string): ChipTone {
  switch (status) {
    case "passed":
      return "ok";
    case "failed":
      return "err";
    case "running":
      return "info";
    case "retrying":
    case "repairing":
      return "warn";
    default: // skipped
      return "muted";
  }
}

/** A clock that re-renders every second while a step is still running, so the
 * elapsed timer ticks; it stops once nothing is running (no needless renders). */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

function phaseCounter(node: PhaseNode): string | null {
  if (node.total > 0) {
    return node.failed > 0 ? `${node.done} / ${node.total} · ${node.failed} failed` : `${node.done} / ${node.total}`;
  }
  if (node.failed > 0) return `${node.failed} failed`;
  return null;
}

function StepRow({ step, now }: { step: StepStateDTO; now: number }) {
  const t = useT();
  const elapsed = stepElapsedMs(step, now);
  return (
    <li
      data-testid="wf-step-row"
      data-step={step.name}
      data-step-key={step.key}
      data-status={step.status}
      style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 0", fontSize: pxToRem(12) }}
    >
      <span style={chipStyle(stepTone(step.status))}>
        {step.status === "repairing"
          ? t("wf.step.repairing", { n: step.repairs || 1 })
          : step.status}
      </span>
      <span style={{ color: "var(--text-paper)", fontFamily: "var(--font-mono)" }}>
        {step.name}
        {step.key ? <span style={{ opacity: 0.6 }}> · {step.key}</span> : null}
      </span>
      {elapsed != null && (
        <span data-testid="wf-step-elapsed" style={{ opacity: 0.7, fontVariantNumeric: "tabular-nums" }}>
          {fmtElapsed(elapsed)}
        </span>
      )}
      {step.attempts > 1 && (
        <span data-testid="wf-step-attempts" style={{ opacity: 0.7 }}>
          ×{step.attempts}
        </span>
      )}
      {step.reason && (
        <span data-testid="wf-step-reason" style={{ color: "var(--err)", opacity: 0.9 }}>
          {step.reason}
        </span>
      )}
    </li>
  );
}

export function WorkflowStepBoard({
  nodes,
  steps,
}: {
  nodes: PhaseNode[];
  steps: StepStateDTO[];
}) {
  const active = steps.some(
    (s) => s.status === "running" || s.status === "retrying" || s.status === "repairing",
  );
  const now = useNow(active);
  const groups = stepBoard(nodes, steps).filter(
    (g) => g.steps.length > 0 || g.node.total > 0 || g.node.failed > 0,
  );
  if (groups.length === 0) return null;
  return (
    <div data-testid="wf-step-board" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {groups.map((g) => {
        const counter = phaseCounter(g.node);
        return (
          <div key={g.node.id} data-phase={g.node.id}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: pxToRem(11),
                textTransform: "uppercase",
                letterSpacing: 0.4,
                color: "var(--text-paper-d)",
              }}
            >
              <span>{g.node.title}</span>
              {counter && <span style={{ opacity: 0.8 }}>· {counter}</span>}
            </div>
            {g.steps.length > 0 && (
              <ul style={{ listStyle: "none", margin: 0, padding: "0 0 0 4px" }}>
                {g.steps.map((s) => (
                  <StepRow key={`${s.name}\u0000${s.key}`} step={s} now={now} />
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}
