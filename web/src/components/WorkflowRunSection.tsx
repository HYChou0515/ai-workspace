/**
 * The single drop-in mount for the workflow surface on an item workspace (#100).
 * Renders nothing for a non-workflow profile (so it's safe to mount on every item).
 * For a workflow profile it shows: a **Run workflow** button (manual §14 — inputs are
 * prepared via the existing file UI), the active/selected run's progress panel
 * (`WorkflowRunPanel`), and the per-item **run history** (manual §14, FE brief E).
 */

import { useEffect, useState } from "react";

import { isRunTerminal, type WorkflowRunDTO } from "../api/workflows";
import { useItemRuns, useStartRun, useWorkflowManifest } from "../hooks/useWorkflow";
import { WorkflowRunPanel } from "./WorkflowRunPanel";

function when(ms: number | null): string {
  if (!ms) return "";
  return new Date(ms).toLocaleString();
}

export function WorkflowRunSection({
  slug,
  itemId,
  profile,
}: {
  slug: string;
  itemId: string;
  profile: string;
}) {
  const { manifest, hasWorkflow } = useWorkflowManifest(slug, profile);
  const runs = useItemRuns(hasWorkflow ? slug : undefined, hasWorkflow ? itemId : undefined);
  const start = useStartRun(slug, itemId);
  const [selected, setSelected] = useState<string | null>(null);

  // Default the selection to the newest run (the list is newest-first).
  const newest = runs.data?.[0]?.run_id ?? null;
  useEffect(() => {
    if (selected === null && newest) setSelected(newest);
  }, [newest, selected]);

  if (!hasWorkflow || !manifest) return null;

  const active = runs.data?.find((r) => !isRunTerminal(r.status));

  const onRun = async () => {
    const res = await start.mutateAsync();
    setSelected(res.run_id);
  };

  return (
    <section
      data-testid="wf-run-section"
      style={{ display: "flex", flexDirection: "column", gap: 12, padding: 12 }}
    >
      <header style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <strong style={{ fontSize: 13 }}>{manifest.title || "Workflow"}</strong>
        <button
          type="button"
          data-testid="wf-run-button"
          disabled={start.isPending || !!active}
          onClick={onRun}
          title={active ? "A run is already in progress" : "Run this workflow"}
          style={{
            marginLeft: "auto",
            padding: "5px 14px",
            borderRadius: 6,
            border: "1px solid var(--accent, var(--info))",
            background: "var(--accent, var(--info))",
            color: "#fff",
            fontWeight: 600,
            cursor: start.isPending || active ? "default" : "pointer",
            opacity: start.isPending || active ? 0.6 : 1,
          }}
        >
          {start.isPending ? "Starting…" : "Run workflow"}
        </button>
      </header>

      {selected && (
        <WorkflowRunPanel
          slug={slug}
          itemId={itemId}
          runId={selected}
          declaredPhases={manifest.phases}
        />
      )}

      {runs.data && runs.data.length > 0 && (
        <details data-testid="wf-run-history">
          <summary style={{ cursor: "pointer", fontSize: 12 }}>
            Run history ({runs.data.length})
          </summary>
          <ul style={{ listStyle: "none", margin: "6px 0 0", padding: 0, fontSize: 12 }}>
            {runs.data.map((r: WorkflowRunDTO) => (
              <li key={r.run_id}>
                <button
                  type="button"
                  data-run={r.run_id}
                  onClick={() => setSelected(r.run_id)}
                  style={{
                    background: r.run_id === selected ? "var(--paper-2)" : "transparent",
                    border: "none",
                    width: "100%",
                    textAlign: "left",
                    padding: "4px 6px",
                    cursor: "pointer",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  <span>{r.status}</span> · <span>{when(r.started)}</span>
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
