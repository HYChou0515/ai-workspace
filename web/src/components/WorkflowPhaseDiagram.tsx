/**
 * Read-only phase diagram (#100, manual §12): the run's phases drawn as an ordered
 * sequence, each with a state (pending / running / passed / failed / awaiting_human
 * / skipped) and, when it loops over a batch, dynamic sub-progress ("3 / 5 · 1
 * failed"). The current phase is emphasised. Purely prop-driven (state derived by
 * `phaseView`), so it renders identically whether fed by a poll or live events.
 */

import type { ChipTone } from "../api/types";
import type { PhaseNode } from "../api/workflows";
import { chipStyle } from "./StatusChip";

export function phaseTone(status: string): ChipTone {
  switch (status) {
    case "passed":
      return "ok";
    case "failed":
      return "err";
    case "running":
      return "info";
    case "awaiting_human":
      return "warn";
    default: // pending | skipped
      return "muted";
  }
}

function subProgress(node: PhaseNode): string | null {
  if (node.total > 0) {
    return node.failed > 0 ? `${node.done} / ${node.total} · ${node.failed} failed` : `${node.done} / ${node.total}`;
  }
  if (node.failed > 0) return `${node.failed} failed`;
  return null;
}

export function WorkflowPhaseDiagram({ nodes }: { nodes: PhaseNode[] }) {
  if (nodes.length === 0) return null;
  return (
    <ol
      data-testid="wf-phase-diagram"
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 6,
        listStyle: "none",
        margin: 0,
        padding: 0,
      }}
    >
      {nodes.map((n, i) => {
        const tone = phaseTone(n.status);
        const prog = subProgress(n);
        return (
          <li
            key={n.id}
            data-phase={n.id}
            data-status={n.status}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <span
              style={{
                ...chipStyle(tone),
                outline: n.current ? "1px solid var(--accent, var(--info))" : "none",
                outlineOffset: 1,
              }}
              title={`${n.title}: ${n.status}`}
            >
              {n.title}
              {prog && <span style={{ opacity: 0.7 }}>· {prog}</span>}
            </span>
            {i < nodes.length - 1 && (
              <span aria-hidden style={{ color: "var(--text-paper-d)", opacity: 0.6 }}>
                →
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
