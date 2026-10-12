import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import type { FileService } from "../api/fileService";
import { qk } from "../api/queryKeys";
import { phaseView, stepOf, workflowApi, type WorkflowRunDTO } from "../api/workflows";
import { useLaunchableWorkflows } from "../hooks/useLaunchableWorkflows";
import { useItemRuns } from "../hooks/useWorkflow";
import { executionPath, statusText } from "../lib/executionTab";
import { pxToRem } from "../lib/pxToRem";
import { WorkflowLaunchDialog } from "./WorkflowLaunchDialog";
import { WorkflowsModal } from "./WorkflowsModal";

function startedText(ms: number | null): string {
  return ms == null ? "" : new Date(ms).toLocaleString();
}

/**
 * The workspace's Workflows sidebar (plan-workflow-out-of-chat D7 / D10): where a
 * workflow is started and its executions are followed, never in a chat (D3).
 *
 * - The workflows this item can start, each with a Start verb; a start goes
 *   through the pre-flight dialog and opens the execution's tab.
 * - "Manage workflows…" opens the Workflows panel (#323: the workspace's own
 *   workflows — run, download, import, templates, schedule times).
 * - The item's executions, newest first, each named by its workflow and start
 *   time, with its status and how far it got ("Step 2 of 3", after NN/g
 *   "Progress Indicators"); a schedule's are marked. One opens as a tab.
 *
 * Presentational over the API hooks; the shell gives it its frame and title.
 */
export function WorkflowsSidebar({
  slug,
  itemId,
  profile,
  fileService,
  onOpenFile,
}: {
  slug: string;
  itemId: string;
  profile: string;
  fileService: FileService;
  onOpenFile: (path: string) => void;
}) {
  const qc = useQueryClient();
  const workflows = useLaunchableWorkflows(slug, itemId, profile);
  const runs = useItemRuns(slug, itemId);
  const [pending, setPending] = useState<string | null>(null);
  const [managing, setManaging] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  const opened = (runId: string) => {
    void qc.invalidateQueries({ queryKey: qk.workflowRuns(slug, itemId) });
    onOpenFile(executionPath(runId));
  };

  const confirm = async () => {
    if (pending == null) return;
    const workflowId = pending;
    setPending(null);
    setFailed(null);
    try {
      opened((await workflowApi.startRun(slug, itemId, workflowId)).run_id);
    } catch (err: unknown) {
      setFailed(err instanceof Error ? err.message : String(err));
    }
  };

  const titleOf = (run: WorkflowRunDTO) =>
    workflows.find((w) => w.id === run.workflow_id)?.title || run.workflow_id || "Workflow";
  const executions = [...(runs.data ?? [])].sort((a, b) => (b.started ?? 0) - (a.started ?? 0));

  return (
    <div className="scrollable" style={{ flex: 1, overflowY: "auto", fontSize: pxToRem(12) }}>
      <section aria-label="Start a workflow" style={{ padding: "6px 14px" }}>
        {workflows.length === 0 && (
          <p style={{ margin: "4px 0", color: "var(--text-paper-d)" }}>
            No workflow to start yet. Ask the agent to make one.
          </p>
        )}
        {workflows.map((w) => (
          <div
            key={w.id}
            style={{ display: "flex", alignItems: "center", gap: 8, padding: "3px 0" }}
          >
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
              {w.title || w.id}
            </span>
            <button
              type="button"
              className="btn"
              data-size="sm"
              aria-label={`Start ${w.title || w.id}`}
              onClick={() => setPending(w.id)}
            >
              Start
            </button>
          </div>
        ))}
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          style={{ marginTop: 6 }}
          onClick={() => setManaging(true)}
        >
          Manage workflows…
        </button>
        {failed && (
          <p role="alert" style={{ margin: "6px 0 0", color: "var(--err)" }}>
            The workflow did not start: {failed}
          </p>
        )}
      </section>

      <section aria-label="Executions" style={{ borderTop: "1px solid var(--paper-3)" }}>
        <div className="caps" style={{ padding: "8px 14px 4px", color: "var(--text-paper-d)" }}>
          Executions
        </div>
        {executions.length === 0 && (
          <p style={{ margin: 0, padding: "2px 14px", color: "var(--text-paper-d)" }}>
            Nothing has run yet.
          </p>
        )}
        {executions.map((r) => {
          const declared = workflows.find((w) => w.id === r.workflow_id)?.phases ?? [];
          const { at, of } = stepOf(phaseView(declared, r));
          return (
            <button
              key={r.run_id}
              type="button"
              data-testid="execution-row"
              data-run={r.run_id}
              onClick={() => onOpenFile(executionPath(r.run_id))}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "6px 14px",
                border: 0,
                background: "transparent",
              }}
            >
              <span style={{ display: "block", fontWeight: 600 }}>{titleOf(r)}</span>
              <span style={{ display: "flex", flexWrap: "wrap", gap: 6, color: "var(--text-paper-d)" }}>
                <span>{statusText(r.status)}</span>
                {of > 0 && <span>{`Step ${at} of ${of}`}</span>}
                {r.schedule_key && <span>Scheduled</span>}
                <span>{startedText(r.started)}</span>
              </span>
            </button>
          );
        })}
      </section>

      {pending != null && (
        <WorkflowLaunchDialog
          slug={slug}
          itemId={itemId}
          workflowId={pending}
          onConfirm={() => void confirm()}
          onClose={() => setPending(null)}
        />
      )}
      {managing && (
        <WorkflowsModal
          slug={slug}
          itemId={itemId}
          fileService={fileService}
          onClose={() => setManaging(false)}
          onRun={opened}
        />
      )}
    </div>
  );
}
