/**
 * Telemetry panel (#11) — the live trace/span view of every LLM-agent run.
 * Reads the history (`GET /monitor`) + the live SSE feed (`GET /monitor/stream`)
 * and folds them into traces: one per run (workflow_name = run flavour, e.g.
 * "Wiki maintainer"), each with its spans (LLM generations + token usage, tool
 * calls, agent steps). Diagnostic surface — when an agent does nothing, this
 * shows whether it called tools or just narrated.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  foldTraces,
  type MonitorApi,
  type MonitorEvent,
  monitorApi,
  type Trace,
  type TraceSpan,
} from "../api/monitor";
import { qk } from "../api/queryKeys";
import { Icon } from "../components/Icon";

const SPAN_LABEL: Record<string, string> = {
  generation: "LLM",
  function: "tool",
  agent: "agent",
  handoff: "handoff",
};

function SpanRow({ span }: { span: TraceSpan }) {
  const kind = SPAN_LABEL[span.type] ?? span.type;
  const tokens =
    span.inputTokens != null || span.outputTokens != null
      ? `↑${span.inputTokens ?? 0} ↓${span.outputTokens ?? 0}`
      : null;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "4px 10px 4px 30px",
        fontSize: "var(--text-body-sm)",
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "var(--text-paper-d2)",
          minWidth: 52,
        }}
      >
        {kind}
      </span>
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {span.label}
      </span>
      {tokens && (
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-paper-d)" }}>
          {tokens}
        </span>
      )}
    </div>
  );
}

function TraceRow({ trace }: { trace: Trace }) {
  const [open, setOpen] = useState(false);
  const totalTokens = trace.inputTokens + trace.outputTokens;
  return (
    <li style={{ borderBottom: "1px solid var(--paper-3)" }}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          width: "100%",
          padding: "11px 12px",
          border: 0,
          background: "none",
          cursor: "pointer",
          textAlign: "left",
          font: "inherit",
        }}
      >
        <Icon name={open ? "chev_d" : "chev_r"} size={13} color="var(--text-paper-d2)" />
        <span
          style={{
            padding: "2px 8px",
            borderRadius: 999,
            fontSize: 11,
            fontWeight: 600,
            background: "var(--accent-soft)",
            color: "var(--accent-h)",
            whiteSpace: "nowrap",
          }}
        >
          {trace.workflowName}
        </span>
        {trace.groupId && (
          <span
            title={trace.groupId}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--text-paper-d2)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              maxWidth: 200,
            }}
          >
            {trace.groupId}
          </span>
        )}
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: "var(--text-paper-d)" }}>
          {trace.spans.length} step{trace.spans.length === 1 ? "" : "s"}
        </span>
        {totalTokens > 0 && (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-paper-d)" }}>
            ↑{trace.inputTokens} ↓{trace.outputTokens}
          </span>
        )}
        {!trace.done && (
          <span style={{ fontSize: 10, color: "var(--accent-h)" }}>live</span>
        )}
      </button>
      {open && (
        <div style={{ paddingBottom: 8 }}>
          {trace.spans.length === 0 ? (
            <div style={{ padding: "4px 10px 4px 30px", fontSize: 12, color: "var(--text-paper-d2)" }}>
              No steps recorded.
            </div>
          ) : (
            trace.spans.map((s) => <SpanRow key={s.id} span={s} />)
          )}
        </div>
      )}
    </li>
  );
}

export function TelemetryPanel({ client = monitorApi }: { client?: MonitorApi }) {
  const [live, setLive] = useState<MonitorEvent[]>([]);
  const { data: history = [] } = useQuery({
    queryKey: qk.monitor,
    queryFn: () => client.getMonitor({ limit: 200 }),
  });

  useEffect(() => {
    const ctrl = new AbortController();
    void (async () => {
      try {
        for await (const ev of client.streamMonitor({ signal: ctrl.signal })) {
          setLive((prev) => [...prev, ev]);
        }
      } catch {
        /* stream aborted on unmount */
      }
    })();
    return () => ctrl.abort();
  }, [client]);

  const traces = useMemo(() => foldTraces([...history, ...live]), [history, live]);

  if (traces.length === 0) {
    return (
      <p
        className="kb-cols__empty"
        role="status"
        style={{ marginTop: 24, color: "var(--text-paper-d)", fontSize: "var(--text-body-sm)" }}
      >
        No activity yet. Run an agent turn (a chat, a wiki build) and its LLM calls + tool calls
        appear here live.
      </p>
    );
  }

  return (
    <ul style={{ listStyle: "none", margin: "18px 0 0", padding: 0 }}>
      {traces.map((t) => (
        <TraceRow key={t.traceId} trace={t} />
      ))}
    </ul>
  );
}
