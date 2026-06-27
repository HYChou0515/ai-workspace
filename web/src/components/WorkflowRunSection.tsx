/**
 * The single drop-in mount for the workflow surface on an item workspace (#100).
 * Renders nothing for a non-workflow profile (so it's safe to mount on every item).
 * For a workflow profile it shows: a **Run workflow** button (manual §14 — inputs are
 * prepared via the existing file UI), the active/selected run's progress panel
 * (`WorkflowRunPanel`), and the per-item **run history** (manual §14, FE brief E).
 */

import { useEffect, useState } from "react";

import { fmtElapsed, isRunTerminal, type WorkflowRunDTO } from "../api/workflows";
import { useItemRuns, useStartRun, useWorkflowManifest } from "../hooks/useWorkflow";
import { useT } from "../lib/i18n";
import { WorkflowLaunchDialog } from "./WorkflowLaunchDialog";
import { WorkflowRunPanel } from "./WorkflowRunPanel";
import { pxToRem } from "../lib/pxToRem";

function when(ms: number | null): string {
  if (!ms) return "";
  return new Date(ms).toLocaleString();
}

function duration(r: WorkflowRunDTO): string {
  if (r.started == null) return "";
  return fmtElapsed(Math.max(0, (r.ended ?? Date.now()) - r.started));
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
  const t = useT();
  const { manifest, hasWorkflow } = useWorkflowManifest(slug, profile);
  const runs = useItemRuns(hasWorkflow ? slug : undefined, hasWorkflow ? itemId : undefined);
  const start = useStartRun(slug, itemId);
  const [selected, setSelected] = useState<string | null>(null);
  // #283: "Run" opens the pre-flight dialog first; the real start happens on confirm.
  const [launching, setLaunching] = useState(false);

  // Default the selection to the newest run (the list is newest-first).
  const newest = runs.data?.[0]?.run_id ?? null;
  useEffect(() => {
    if (selected === null && newest) setSelected(newest);
  }, [newest, selected]);

  if (!hasWorkflow || !manifest) return null;

  const active = runs.data?.find((r) => !isRunTerminal(r.status));

  const onConfirm = async () => {
    setLaunching(false);
    const res = await start.mutateAsync();
    setSelected(res.run_id);
  };

  return (
    <section
      data-testid="wf-run-section"
      style={{ display: "flex", flexDirection: "column", gap: 12, padding: 12 }}
    >
      <header style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <strong style={{ fontSize: pxToRem(13) }}>{manifest.title || "Workflow"}</strong>
        <button
          type="button"
          data-testid="wf-run-button"
          disabled={start.isPending || !!active}
          onClick={() => setLaunching(true)}
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

      {launching && (
        <WorkflowLaunchDialog
          slug={slug}
          itemId={itemId}
          workflowId={manifest.id}
          onConfirm={onConfirm}
          onClose={() => setLaunching(false)}
        />
      )}

      {selected && (
        <WorkflowRunPanel
          slug={slug}
          itemId={itemId}
          runId={selected}
          declaredPhases={manifest.phases}
        />
      )}

      {/* #283 (Design D): runs are first-class — a persistent, browsable list (not a
          collapsed dropdown), so a finished run is easy to find and reopen. Each row is
          its own run with a status + when + how-long; clicking selects its panel. */}
      {runs.data && runs.data.length > 0 && (
        <section data-testid="wf-run-list" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d2)" }}>
            {t("wf.runs.title")} ({runs.data.length})
          </span>
          <ul style={{ listStyle: "none", margin: 0, padding: 0, fontSize: pxToRem(12) }}>
            {runs.data.map((r: WorkflowRunDTO) => (
              <li key={r.run_id}>
                <button
                  type="button"
                  data-run={r.run_id}
                  data-testid="wf-run-list-item"
                  onClick={() => setSelected(r.run_id)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    background: r.run_id === selected ? "var(--paper-2)" : "transparent",
                    border: "none",
                    borderLeft:
                      r.run_id === selected
                        ? "2px solid var(--accent, var(--info))"
                        : "2px solid transparent",
                    width: "100%",
                    textAlign: "left",
                    padding: "5px 8px",
                    borderRadius: 4,
                    cursor: "pointer",
                  }}
                >
                  <span style={{ fontWeight: r.run_id === selected ? 600 : 400 }}>{r.status}</span>
                  <span style={{ color: "var(--text-paper-d2)" }}>·</span>
                  <span style={{ color: "var(--text-paper-d)" }}>{when(r.started)}</span>
                  {duration(r) && (
                    <span style={{ marginLeft: "auto", color: "var(--text-paper-d2)", fontFamily: "var(--font-mono)" }}>
                      {duration(r)}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}
