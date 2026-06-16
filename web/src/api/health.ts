/**
 * Health / diagnostics API client (#51). Wire shapes mirror
 * `api/health_routes.py`: every registered probe with its latest cached
 * result, plus the global `running` flag; POST runs a round (all or one).
 * Mock/real swap on the same `VITE_USE_MOCK` switch as `./index`.
 */

import { apiFetch } from "./http";

export type HealthStatus = "pass" | "fail" | "skip" | "error";

export type HealthCheckRow = {
  check_id: string;
  description: string;
  /** Connectivity-grade probe (runs synchronously at startup). */
  fast: boolean;
  /** Latest result; null = never run yet. */
  status: HealthStatus | null;
  detail: string;
  latency_ms: number | null;
  checked_at: number | null; // epoch ms
};

export type HealthOut = {
  /** A probe round is in flight — poll until it settles. */
  running: boolean;
  checks: HealthCheckRow[];
};

export type HealthApi = {
  getChecks(): Promise<HealthOut>;
  /** Trigger a probe round — all checks, or one by id. 202-style:
   * resolves as soon as the round is STARTED; poll getChecks. */
  runChecks(checkId?: string): Promise<{ started: boolean }>;
};

export const realHealthApi: HealthApi = {
  async getChecks() {
    const r = await apiFetch("/health/checks");
    if (!r.ok) throw new Error(`health checks failed: ${r.status}`);
    return (await r.json()) as HealthOut;
  },
  async runChecks(checkId?: string) {
    const r = await apiFetch("/health/checks/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(checkId ? { check_id: checkId } : {}),
    });
    if (!r.ok) throw new Error(`run checks failed: ${r.status}`);
    return (await r.json()) as { started: boolean };
  },
};

/** Dev-mode (`VITE_USE_MOCK=1`) client: a canned panel that "runs" a
 * round instantly so the dot/page interactions are demoable offline. */
const mockChecks: HealthCheckRow[] = [
  "embedder-default",
  "insight-extraction",
  "vlm-describe",
  "agent-workspace",
].map((id, i) => ({
  check_id: id,
  description: `${id} probe`,
  fast: i === 0,
  status: "pass",
  detail: "",
  latency_ms: 40 + i * 7,
  checked_at: Date.now(),
}));

export const mockHealthApi: HealthApi = {
  async getChecks() {
    return { running: false, checks: mockChecks };
  },
  async runChecks() {
    for (const c of mockChecks) c.checked_at = Date.now();
    return { started: true };
  },
};

const useMock = import.meta.env.VITE_USE_MOCK === "1";

export const healthApi: HealthApi = useMock ? mockHealthApi : realHealthApi;

/* ── replay diagnostics (#51 P6) ──────────────────────────────────── */

export type ReplayToolCallOut = {
  /** A tool the AI WANTED to call during the replay — never executed. */
  name: string;
  arguments: Record<string, unknown>;
};

export type ReplayOriginal = {
  role: string;
  content: string;
  tool_name: string | null;
  tool_args: Record<string, unknown> | null;
};

/** What the replay sent the model (#69 observability) — the tool-calling
 * knobs to compare against the live turn's logged trace. */
export type ReplayRequest = {
  model: string;
  endpoint: string;
  tools: string[];
  parallel_tool_calls: string;
  tool_choice: string;
};

export type ReplayOut = {
  text: string;
  reasoning: string;
  tool_calls: ReplayToolCallOut[];
  model: string;
  latency_ms: number;
  note: string;
  /** Turn replays echo the persisted message for side-by-side view. */
  original: ReplayOriginal | null;
  /** Turn replays echo what they sent the model. */
  request: ReplayRequest | null;
};

export type ReplayTurnReq = {
  source: "rca" | "kb";
  thread_id: string;
  message_index: number;
};

/** Non-2xx replay responses carry an explanation (409 = nothing to
 * replay, 422 = not a model-produced message…) — surface it. */
export class ReplayError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

async function replayFetch(path: string, body: unknown): Promise<ReplayOut> {
  const r = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = `replay failed: ${r.status}`;
    try {
      const data = (await r.json()) as { detail?: string };
      if (data.detail) detail = data.detail;
    } catch {
      // non-JSON error body — keep the generic message
    }
    throw new ReplayError(r.status, detail);
  }
  return (await r.json()) as ReplayOut;
}

export type ReplayApi = {
  replayTurn(req: ReplayTurnReq): Promise<ReplayOut>;
  replayDoc(documentId: string): Promise<ReplayOut>;
};

export const realReplayApi: ReplayApi = {
  replayTurn: (req) => replayFetch("/health/replay/turn", req),
  replayDoc: (documentId) => replayFetch("/health/replay/doc", { document_id: documentId }),
};

export const mockReplayApi: ReplayApi = {
  async replayTurn() {
    return {
      text: "Mock replay answer — the current model would say this.",
      reasoning: "mock thinking",
      tool_calls: [],
      model: "mock/model",
      latency_ms: 420,
      note: "",
      original: { role: "assistant", content: "Original answer.", tool_name: null, tool_args: null },
      request: {
        model: "mock/model",
        endpoint: "default",
        tools: ["kb_search"],
        parallel_tool_calls: "unset",
        tool_choice: "auto (unset)",
      },
    };
  },
  async replayDoc() {
    return {
      text: '{"insights": []}',
      reasoning: "",
      tool_calls: [],
      model: "mock/model",
      latency_ms: 800,
      note: "no insights would be extracted from this response",
      original: null,
      request: null,
    };
  },
};

export const replayApi: ReplayApi = useMock ? mockReplayApi : realReplayApi;
