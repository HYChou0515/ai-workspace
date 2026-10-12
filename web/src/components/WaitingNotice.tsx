import { useState } from "react";

import type { WorkflowRunDTO } from "../api/workflows";
import { useLaunchableWorkflows } from "../hooks/useLaunchableWorkflows";
import { useItemRuns } from "../hooks/useWorkflow";
import { executionName, executionPath } from "../lib/executionTab";
import { pxToRem } from "../lib/pxToRem";

/** Which wait this is: a dismissal holds for this gate (or this proposed change)
 * of this execution, and the next one asks again. */
function waitKey(r: WorkflowRunDTO): string {
  return `${r.run_id}:${r.pending_decision?.phase ?? (r.pending_steer ? "steer" : "")}`;
}

/**
 * An execution waiting for the person, said wherever they are in the workspace
 * (plan-workflow-out-of-chat P9 / D7). A non-modal notification (NN/g,
 * "Indicators, Validations, and Notifications": it reports an event the person
 * did not cause, without blocking what they are doing). It asks for an action,
 * so no timer takes it away: it stays until acted on or dismissed. It names
 * the execution and what it waits for, and Open
 * goes to that execution's tab, where the gate is decided. Not shown while that
 * tab is in front — the person is already there.
 */
export function WaitingNotice({
  slug,
  itemId,
  profile,
  activePath,
  onOpen,
}: {
  slug: string;
  itemId: string;
  profile: string;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  const runs = useItemRuns(slug, itemId).data ?? [];
  const workflows = useLaunchableWorkflows(slug, itemId, profile);
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(new Set());

  const waitingRuns = runs
    .filter((r) => r.status === "awaiting_human" && (r.pending_decision || r.pending_steer))
    .filter((r) => !dismissed.has(waitKey(r)))
    .sort((a, b) => (b.started ?? 0) - (a.started ?? 0));
  const shown = waitingRuns.find((r) => executionPath(r.run_id) !== activePath);
  if (!shown) return null;
  // The others, but not the one in front: the person is looking at it.
  const more = waitingRuns.filter((r) => r !== shown && executionPath(r.run_id) !== activePath).length;
  const title =
    workflows.find((w) => w.id === shown.workflow_id)?.title || shown.workflow_id || "A workflow";
  const what = shown.pending_decision
    ? shown.pending_decision.title || "A decision"
    : "A change to confirm";

  return (
    <div
      role="status"
      data-testid="waiting-notice"
      style={{
        position: "fixed",
        right: 18,
        bottom: 18,
        zIndex: 55,
        maxWidth: "min(380px, calc(100vw - 32px))",
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "10px 12px",
        borderRadius: "var(--radius-btn)",
        background: "var(--paper-2)",
        border: "1px solid var(--warn)",
        boxShadow: "0 6px 20px rgba(0,0,0,0.18)",
        fontSize: pxToRem(13),
        lineHeight: 1.5,
      }}
    >
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontWeight: 600, overflowWrap: "anywhere" }}>
          {`${executionName(title, shown.started)} is waiting for you`}
        </div>
        <div style={{ color: "var(--text-paper-d)", overflowWrap: "anywhere" }}>{what}</div>
        {more > 0 && (
          <div style={{ color: "var(--text-paper-d)", fontSize: pxToRem(11) }}>{`${more} more waiting`}</div>
        )}
      </div>
      <button
        type="button"
        className="btn"
        data-variant="primary"
        data-size="sm"
        onClick={() => onOpen(executionPath(shown.run_id))}
      >
        Open
      </button>
      <button
        type="button"
        className="btn"
        data-size="sm"
        aria-label="Dismiss"
        onClick={() => setDismissed((d) => new Set(d).add(waitKey(shown)))}
      >
        ✕
      </button>
    </div>
  );
}
