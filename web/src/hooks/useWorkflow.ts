/**
 * TanStack Query hooks for the workflow run surface (#100). Reads (profiles /
 * runs / one run) are `useQuery`; start / cancel / decide are `useMutation` that
 * invalidate the affected keys. The single-run query **polls while the run is
 * live** (non-terminal) so the phase diagram + status advance without wiring the
 * raw SSE stream — the run persists its per-phase progress on every step (§12).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { qk } from "../api/queryKeys";
import { isRunTerminal, workflowApi, type WorkflowRunDTO } from "../api/workflows";

export function useWorkflowProfiles(slug: string | undefined) {
  return useQuery({
    queryKey: qk.workflowProfiles(slug ?? ""),
    queryFn: () => workflowApi.listProfiles(slug!),
    enabled: !!slug,
  });
}

/** The workflow manifest for `profile`, or null when that profile has no workflow. */
export function useWorkflowManifest(slug: string | undefined, profile: string | undefined) {
  const q = useWorkflowProfiles(slug);
  const entry = q.data?.find((p) => p.name === profile);
  return { ...q, manifest: entry?.workflow ?? null, hasWorkflow: !!entry?.has_workflow };
}

export function useItemRuns(slug: string | undefined, itemId: string | undefined) {
  return useQuery({
    queryKey: qk.workflowRuns(slug ?? "", itemId ?? ""),
    queryFn: () => workflowApi.listRuns(slug!, itemId!),
    enabled: !!slug && !!itemId,
  });
}

export function useRun(
  slug: string | undefined,
  itemId: string | undefined,
  runId: string | undefined,
) {
  return useQuery<WorkflowRunDTO>({
    queryKey: qk.workflowRun(slug ?? "", itemId ?? "", runId ?? ""),
    queryFn: () => workflowApi.getRun(slug!, itemId!, runId!),
    enabled: !!slug && !!itemId && !!runId,
    // Poll while the run is live; stop once it reaches a terminal status.
    refetchInterval: (q) => (q.state.data && isRunTerminal(q.state.data.status) ? false : 1000),
  });
}

export function useStartRun(slug: string, itemId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => workflowApi.startRun(slug, itemId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.workflowRuns(slug, itemId) });
    },
  });
}

export function useCancelRun(slug: string, itemId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => workflowApi.cancelRun(slug, itemId, runId),
    onSuccess: (_v, runId) => {
      qc.invalidateQueries({ queryKey: qk.workflowRun(slug, itemId, runId) });
      qc.invalidateQueries({ queryKey: qk.workflowRuns(slug, itemId) });
    },
  });
}

export function useDecide(slug: string, itemId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { choice: string; input?: string }) =>
      workflowApi.decide(slug, itemId, runId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.workflowRun(slug, itemId, runId) });
    },
  });
}
