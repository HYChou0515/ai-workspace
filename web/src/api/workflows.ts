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
  /** One-line human description for the launcher card. */
  description?: string;
  /** A short kind pill — e.g. "batch" | "single". */
  tag?: string;
  /** One-line inputs hint (e.g. where to drop files). */
  hint?: string;
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

/** One input-file rewrite in a steer plan (#288): the full new `content` for `path`. */
export type SteerInputEdit = { path: string; content: string };

/** A proposed steer awaiting confirm (#288, manual §10): rewrite `input_edits` +
 * `invalidate` steps (force re-run). `instruction` is the operator's free-text ask;
 * `rationale` is the steerer's one-line summary. The FE renders the confirm card. */
export type SteerPlan = {
  instruction: string;
  rationale: string;
  input_edits: SteerInputEdit[];
  invalidate: string[];
  decided_by: string;
};

/** One step's status on the board (#178). Bounded by collapse: loop elements
 * (`key !== ""`) appear only while running; distinct-named steps persist with their
 * final status + duration. `started`/`ended` are server epoch ms (reload-safe). */
export type StepStateDTO = {
  phase: string;
  name: string;
  key: string;
  status: string; // running | retrying | passed | skipped | failed
  attempts: number;
  reason: string;
  started: number | null;
  ended: number | null;
};

export type WorkflowRunDTO = {
  run_id: string;
  item_id: string;
  /** Which of the profile's workflows this run executes (manual §4); "" for the
   * legacy singular workflow. Durable on the run record (P8). */
  workflow_id?: string;
  captured_user: string;
  status: RunStatus;
  current_phase: string;
  phases: PhaseState[];
  /** Per-step board (#178); empty for runs written before #178. */
  steps: StepStateDTO[];
  failures: Failure[];
  started: number | null;
  ended: number | null;
  result: Record<string, unknown> | null;
  pending_decision: PendingDecision | null;
  /** #288: a steer plan awaiting confirm (the FE renders the steer card vs. the gate
   * card by which pending field is set). Absent on runs written before #288. */
  pending_steer?: SteerPlan | null;
};

/** One pre-flight checklist line in the launch dialog (#283). `severity` is
 * "required" (a failing one blocks 'Run') or "advisory" (a warning you can proceed past). */
export type PreflightCheckDTO = {
  label: string;
  ok: boolean;
  severity: "required" | "advisory";
  reason: string;
};

/** What the launch dialog shows BEFORE a run starts (#283): the workflow's identity +
 * phases, plus (when the author wrote a `preflight`) a human summary of what the run
 * will do and a checklist of its preconditions. `can_run` is false when a required
 * check fails; `has_preflight` is false when the author wrote none (phases-only preview). */
export type PreflightPreviewDTO = {
  workflow_id: string;
  title: string;
  description: string;
  phases: PhaseDef[];
  summary: string;
  checks: PreflightCheckDTO[];
  can_run: boolean;
  has_preflight: boolean;
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

/** A phase plus the steps the board shows under it (#178). */
export type PhaseStepGroup = { node: PhaseNode; steps: StepStateDTO[] };

/**
 * Group the run's persisted steps under their phase node, preserving the diagram's
 * phase order (#178). Loop elements are already collapsed server-side, so a phase's
 * `steps` is just its distinct-named steps + the currently-running element(s); the
 * `done / total · N failed` counter on the node carries the collapsed remainder.
 * Pure — the board renders this, so it's unit-tested without the network.
 */
export function stepBoard(nodes: PhaseNode[], steps: StepStateDTO[]): PhaseStepGroup[] {
  return nodes.map((node) => ({
    node,
    steps: steps.filter((s) => s.phase === node.id),
  }));
}

/** How long a step has run, in ms: `ended - started` once done, else `now - started`
 * (it keeps ticking). `null` when the step never recorded a start (e.g. a cache
 * skip), so the board can omit the timer rather than show a bogus 0. */
export function stepElapsedMs(step: StepStateDTO, now: number): number | null {
  if (step.started == null) return null;
  return Math.max(0, (step.ended ?? now) - step.started);
}

/** Format an elapsed duration as `m:ss` (or `h:mm:ss` past an hour) for the board. */
export function fmtElapsed(ms: number): string {
  const total = Math.floor(ms / 1000);
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

async function jsonOrThrow(r: Response, what: string): Promise<unknown> {
  if (!r.ok) throw new Error(`${what} failed: ${r.status}`);
  return r.json();
}

const base = (slug: string, itemId: string) =>
  `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}`;

/** Fetch the conversation as the re-ingestable `.chat.json` (issue #39), via the
 * app-scoped route (#95). Validates it's actually the chat file: a misrouted GET
 * falls through to the SPA and returns `text/html` 200, which the browser used to
 * save silently as `export-chat.html`. We surface that as a loud error instead of
 * a download of the app shell (#100). */
export async function fetchChatExport(slug: string, itemId: string): Promise<Blob> {
  const res = await apiFetch(`${base(slug, itemId)}/export-chat`);
  const contentType = res.headers.get("content-type") ?? "";
  if (!res.ok || !contentType.includes("application/json")) {
    throw new Error("匯出失敗：伺服器沒有回傳對話檔，請稍後再試或回報問題。");
  }
  return res.blob();
}

/** Browser download wrapper around {@link fetchChatExport} — triggers the save
 * once the response is validated, so a failure shows an error rather than saving
 * the SPA's HTML. */
export async function downloadChatExport(slug: string, itemId: string): Promise<void> {
  const blob = await fetchChatExport(slug, itemId);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${itemId}.chat.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

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
    chatId = "",
  ): Promise<{ run_id: string; item_id: string; chat_id: string }> {
    // topic-hub §3/§4: launching opens a workflow CHAT (returns its chat_id) and
    // `workflow_id` picks which of the profile's workflows to run. #343: with a
    // `chatId`, the run TAKES OVER that existing chat (the one prepared in) instead
    // of opening a fresh one — the returned chat_id is that same chat.
    const params = new URLSearchParams();
    if (workflowId) params.set("workflow_id", workflowId);
    if (chatId) params.set("chat_id", chatId);
    const qs = params.toString() ? `?${params}` : "";
    return jsonOrThrow(
      await apiFetch(`${base(slug, itemId)}/run${qs}`, { method: "POST" }),
      "start run",
    ) as Promise<{ run_id: string; item_id: string; chat_id: string }>;
  },

  async previewRun(
    slug: string,
    itemId: string,
    workflowId = "",
  ): Promise<PreflightPreviewDTO> {
    // #283: the launch dialog's pre-flight — what this workflow will do + whether its
    // preconditions are met, WITHOUT starting a run.
    const qs = workflowId ? `?workflow_id=${encodeURIComponent(workflowId)}` : "";
    return jsonOrThrow(
      await apiFetch(`${base(slug, itemId)}/runs/preview${qs}`),
      "preview run",
    ) as Promise<PreflightPreviewDTO>;
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

  async steer(slug: string, itemId: string, runId: string, instruction: string): Promise<void> {
    // #288 (manual §10): redirect a run in words. Stops it first if it is still going,
    // then the read-only steerer proposes a plan the human confirms.
    const r = await apiFetch(`${base(slug, itemId)}/runs/${encodeURIComponent(runId)}/steer`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ instruction }),
    });
    if (!r.ok) throw new Error(`steer failed: ${r.status}`);
  },

  async confirmSteer(
    slug: string,
    itemId: string,
    runId: string,
    approve: boolean,
  ): Promise<void> {
    // #288: apply the proposed plan + resume the run (approve), or discard it (reject).
    const r = await apiFetch(
      `${base(slug, itemId)}/runs/${encodeURIComponent(runId)}/steer/confirm`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ approve }),
      },
    );
    if (!r.ok) throw new Error(`steer confirm failed: ${r.status}`);
  },
};
