/**
 * Telemetry (monitor) API client — issue #11. The backend mirrors the OpenAI
 * Agents SDK trace stream (a Trace per run + nested Spans for every LLM
 * generation with token usage, tool call, agent step) into an IMonitor; this
 * reads the history (`GET /monitor`) and the live SSE feed (`GET
 * /monitor/stream`). `foldTraces` reduces the flat event stream into the
 * trace → span tree the Telemetry panel renders.
 */

import { apiFetch } from "./http";
import { parseSseStream } from "./sse";

export type SpanData = {
  /** "generation" | "function" | "agent" | "handoff" | … */
  type?: string;
  name?: string;
  model?: string;
  usage?: { input_tokens?: number; output_tokens?: number };
};

/** One raw monitor event — the SDK's trace/span `export()` plus our `kind`. */
export type MonitorEvent = {
  kind: string; // trace_start | trace_end | span_start | span_end
  group_id?: string | null;
  id?: string; // trace id (trace events) / span id (span events)
  trace_id?: string; // span events
  workflow_name?: string; // trace events (the run flavour label)
  span_data?: SpanData;
};

export type TraceSpan = {
  id: string;
  type: string;
  label: string; // model (generation) / tool name (function) / agent name
  inputTokens?: number;
  outputTokens?: number;
};

export type Trace = {
  traceId: string;
  workflowName: string;
  groupId: string | null;
  spans: TraceSpan[];
  done: boolean;
  inputTokens: number; // summed over the trace's generation spans
  outputTokens: number;
};

/** Reduce the flat event stream into traces (newest first), each with its
 * spans. Idempotent against overlap (history + live both carry the same
 * event) — traces merge by id, spans dedup by id. */
export function foldTraces(events: MonitorEvent[]): Trace[] {
  const byId = new Map<string, Trace & { _seq: number; _seen: Set<string> }>();
  let seq = 0;
  const ensure = (id: string) => {
    let t = byId.get(id);
    if (!t) {
      t = {
        traceId: id,
        workflowName: "trace",
        groupId: null,
        spans: [],
        done: false,
        inputTokens: 0,
        outputTokens: 0,
        _seq: seq++,
        _seen: new Set(),
      };
      byId.set(id, t);
    }
    return t;
  };

  for (const e of events) {
    if (e.kind === "trace_start" || e.kind === "trace_end") {
      const t = ensure(e.id ?? "");
      if (e.workflow_name) t.workflowName = e.workflow_name;
      if (e.group_id != null) t.groupId = e.group_id;
      if (e.kind === "trace_end") t.done = true;
    } else if (e.kind === "span_end") {
      const t = ensure(e.trace_id ?? "");
      if (e.group_id != null && t.groupId == null) t.groupId = e.group_id;
      const sid = e.id ?? `s${t.spans.length}`;
      if (t._seen.has(sid)) continue; // dedup history/live overlap
      t._seen.add(sid);
      const sd = e.span_data ?? {};
      const usage = sd.usage ?? {};
      t.spans.push({
        id: sid,
        type: sd.type ?? "span",
        label: sd.model ?? sd.name ?? sd.type ?? "span",
        inputTokens: usage.input_tokens,
        outputTokens: usage.output_tokens,
      });
      t.inputTokens += usage.input_tokens ?? 0;
      t.outputTokens += usage.output_tokens ?? 0;
    }
  }
  return [...byId.values()].sort((a, b) => b._seq - a._seq);
}

export interface MonitorApi {
  getMonitor(opts?: { groupId?: string; limit?: number }): Promise<MonitorEvent[]>;
  streamMonitor(opts?: { groupId?: string; signal?: AbortSignal }): AsyncGenerator<MonitorEvent>;
}

export const realMonitorApi: MonitorApi = {
  async getMonitor({ groupId, limit } = {}) {
    const qs = new URLSearchParams();
    if (groupId) qs.set("group_id", groupId);
    if (limit != null) qs.set("limit", String(limit));
    const url = qs.size ? `/monitor?${qs.toString()}` : "/monitor";
    const r = await apiFetch(url);
    if (!r.ok) throw new Error(`monitor failed: ${r.status}`);
    return r.json();
  },
  async *streamMonitor({ groupId, signal } = {}) {
    const qs = groupId ? `?group_id=${encodeURIComponent(groupId)}` : "";
    const r = await apiFetch(`/monitor/stream${qs}`, { signal });
    if (!r.ok || !r.body) throw new Error(`monitor stream failed: ${r.status}`);
    yield* parseSseStream<MonitorEvent>(r.body);
  },
};

/* ------------------------------- mock ------------------------------- */

const _mockEvents: MonitorEvent[] = [
  { kind: "trace_start", id: "t1", group_id: "inv-1", workflow_name: "RCA turn" },
  {
    kind: "span_end",
    id: "s1",
    trace_id: "t1",
    span_data: { type: "generation", model: "qwen3:14b", usage: { input_tokens: 812, output_tokens: 143 } },
  },
  { kind: "span_end", id: "s2", trace_id: "t1", span_data: { type: "function", name: "exec" } },
  { kind: "trace_end", id: "t1", group_id: "inv-1", workflow_name: "RCA turn" },
  { kind: "trace_start", id: "t2", group_id: "col-9", workflow_name: "Wiki maintainer" },
  {
    kind: "span_end",
    id: "s3",
    trace_id: "t2",
    span_data: { type: "generation", model: "qwen3:14b", usage: { input_tokens: 1530, output_tokens: 420 } },
  },
  { kind: "trace_end", id: "t2", group_id: "col-9", workflow_name: "Wiki maintainer" },
];

export const mockMonitorApi: MonitorApi = {
  async getMonitor() {
    return _mockEvents;
  },
  async *streamMonitor() {
    // The mock feed is the history; nothing live arrives.
  },
};

const useMock = import.meta.env.VITE_USE_MOCK === "1";
export const monitorApi: MonitorApi = useMock ? mockMonitorApi : realMonitorApi;
