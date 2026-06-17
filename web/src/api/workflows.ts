/**
 * Workflow run API client (#100, manual §14) — start / poll / list / cancel a
 * headless run, and answer a `human_gate`. A run is a turn on the item, so its
 * agent activity already streams into the normal chat (reused via AgentEntryView);
 * this client drives the *run* surface (phase progress + status + the decision).
 *
 * `phaseView` merges the static phase skeleton (from the profile's MANIFEST) with
 * the run's live per-phase progress into the ordered list the diagram renders —
 * a pure function, unit-tested without the network.
 */

import { apiFetch } from "./http";

export type PhaseDef = { id: string; title?: string };

export type WorkflowManifestDTO = {
  /** Stable id within the profile (manual §4); "" for a legacy singular workflow. */
  id: string;
  title: string;
  phases: PhaseDef[];
  input_json: string;
  config?: Record<string, unknown>;
};

export type ProfileDTO = {
  name: string;
  title: string;
  description: string;
  has_workflow: boolean;
  /** Legacy singular block — kept for back-compat; prefer `workflows`. */
  workflow: WorkflowManifestDTO | null;
  /** topic-hub §4: every workflow the profile offers (the new-chat picker lists these). */
  workflows: WorkflowManifestDTO[];
};

export type RunStatus =
  | "pending"
  | "running"
  | "awaiting_human"
  | "done"
  | "error"
  | "cancelled";

export type PhaseState = {
  phase: string;
  status: string; // pending | running | passed | failed | skipped
  done: number;
  total: number;
  failed: number;
};

export type PendingDecision = {
  phase: string;
  title: string;
  summary: string;
  allow: string[];
  decided_by: string;
};

export type Failure = { key: string; error: string; phase: string };

export type WorkflowRunDTO = {
  run_id: string;
  item_id: string;
  captured_user: string;
  status: RunStatus;
  current_phase: string;
  phases: PhaseState[];
  failures: Failure[];
  started: number | null;
  ended: number | null;
  result: Record<string, unknown> | null;
  pending_decision: PendingDecision | null;
};

export const RUN_TERMINAL: RunStatus[] = ["done", "error", "cancelled"];

export function isRunTerminal(status: RunStatus): boolean {
  return RUN_TERMINAL.includes(status);
}

/** A phase node for the diagram: the manifest skeleton + the run's live state. */
export type PhaseNode = {
  id: string;
  title: string;
  status: string; // pending | running | passed | failed | skipped | awaiting_human
  done: number;
  total: number;
  failed: number;
  current: boolean;
};

/**
 * Merge the manifest's declared phases with the run's per-phase progress into the
 * ordered diagram list. Declared phases keep their order + titles; a phase the run
 * touched that the manifest didn't declare is appended (the §12 drift caveat). The
 * phase named by `pending_decision` reads as `awaiting_human`.
 */
export function phaseView(
  declared: PhaseDef[],
  run: WorkflowRunDTO | null | undefined,
): PhaseNode[] {
  const byId = new Map<string, PhaseState>();
  for (const p of run?.phases ?? []) byId.set(p.phase, p);
  const awaiting =
    run?.status === "awaiting_human" ? run?.pending_decision?.phase : undefined;
  const nodes: PhaseNode[] = [];
  const seen = new Set<string>();
  const push = (id: string, title: string) => {
    seen.add(id);
    const st = byId.get(id);
    nodes.push({
      id,
      title: title || id,
      status: awaiting === id ? "awaiting_human" : (st?.status ?? "pending"),
      done: st?.done ?? 0,
      total: st?.total ?? 0,
      failed: st?.failed ?? 0,
      current: run?.current_phase === id,
    });
  };
  for (const d of declared) push(d.id, d.title ?? d.id);
  for (const p of run?.phases ?? []) if (!seen.has(p.phase)) push(p.phase, p.phase);
  return nodes;
}

async function jsonOrThrow(r: Response, what: string): Promise<unknown> {
  if (!r.ok) throw new Error(`${what} failed: ${r.status}`);
  return r.json();
}

const base = (slug: string, itemId: string) =>
  `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}`;

export const workflowApi = {
  async listProfiles(slug: string): Promise<ProfileDTO[]> {
    return jsonOrThrow(
      await apiFetch(`/a/${encodeURIComponent(slug)}/profiles`),
      "list profiles",
    ) as Promise<ProfileDTO[]>;
  },

  async startRun(
    slug: string,
    itemId: string,
    workflowId = "",
  ): Promise<{ run_id: string; item_id: string; chat_id: string }> {
    // topic-hub §3/§4: launching opens a workflow CHAT (returns its chat_id) and
    // `workflow_id` picks which of the profile's workflows to run.
    const qs = workflowId ? `?workflow_id=${encodeURIComponent(workflowId)}` : "";
    return jsonOrThrow(
      await apiFetch(`${base(slug, itemId)}/run${qs}`, { method: "POST" }),
      "start run",
    ) as Promise<{ run_id: string; item_id: string; chat_id: string }>;
  },

  async getRun(slug: string, itemId: string, runId: string): Promise<WorkflowRunDTO> {
    return jsonOrThrow(
      await apiFetch(`${base(slug, itemId)}/runs/${encodeURIComponent(runId)}`),
      "get run",
    ) as Promise<WorkflowRunDTO>;
  },

  async listRuns(slug: string, itemId: string): Promise<WorkflowRunDTO[]> {
    return jsonOrThrow(
      await apiFetch(`${base(slug, itemId)}/runs`),
      "list runs",
    ) as Promise<WorkflowRunDTO[]>;
  },

  async cancelRun(slug: string, itemId: string, runId: string): Promise<void> {
    const r = await apiFetch(`${base(slug, itemId)}/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
    });
    if (!r.ok) throw new Error(`cancel run failed: ${r.status}`);
  },

  async decide(
    slug: string,
    itemId: string,
    runId: string,
    body: { choice: string; input?: string },
  ): Promise<void> {
    const r = await apiFetch(`${base(slug, itemId)}/runs/${encodeURIComponent(runId)}/decisions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`decision failed: ${r.status}`);
  },
};
