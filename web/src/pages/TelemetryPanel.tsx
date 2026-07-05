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
  type RowsPoint,
  type Trace,
  type TraceSpan,
} from "../api/monitor";
import { qk } from "../api/queryKeys";
import { Icon } from "../components/Icon";
import { type MsgKey, useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";

// span.type → a localized label; `generation` reads as the literal "LLM".
const SPAN_KEY: Record<string, MsgKey> = {
  function: "telemetry.span.tool",
  agent: "telemetry.span.agent",
  handoff: "telemetry.span.handoff",
};

function SpanRow({ span }: { span: TraceSpan }) {
  const t = useT();
  const kind =
    span.type === "generation" ? "LLM" : span.type in SPAN_KEY ? t(SPAN_KEY[span.type]) : span.type;
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
          fontSize: pxToRem(10),
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
        <span style={{ fontFamily: "var(--font-mono)", fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
          {tokens}
        </span>
      )}
    </div>
  );
}

function TraceRow({ trace }: { trace: Trace }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const totalTokens = trace.inputTokens + trace.outputTokens;
  const stepCount = trace.spans.length;
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
            fontSize: pxToRem(11),
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
              fontSize: pxToRem(11),
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
        <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
          {t(stepCount === 1 ? "telemetry.step" : "telemetry.steps", { n: stepCount })}
        </span>
        {totalTokens > 0 && (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
            ↑{trace.inputTokens} ↓{trace.outputTokens}
          </span>
        )}
        {!trace.done && (
          <span style={{ fontSize: pxToRem(10), color: "var(--accent-h)" }}>{t("telemetry.live")}</span>
        )}
      </button>
      {open && (
        <div style={{ paddingBottom: 8 }}>
          {trace.spans.length === 0 ? (
            <div style={{ padding: "4px 10px 4px 30px", fontSize: pxToRem(12), color: "var(--text-paper-d2)" }}>
              {t("telemetry.noSteps")}
            </div>
          ) : (
            trace.spans.map((s) => <SpanRow key={s.id} span={s} />)
          )}
        </div>
      )}
    </li>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 96 }}>
      <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d2)" }}>{label}</span>
      <span style={{ fontSize: "var(--text-body)", fontWeight: 600, fontFamily: "var(--font-mono)" }}>
        {value}
      </span>
    </div>
  );
}

/** A dependency-free sparkline of the WorkspaceFile row-count trend (#407). */
function Sparkline({ points }: { points: RowsPoint[] }) {
  const t = useT();
  if (points.length < 2) return null;
  const rows = points.map((p) => p.rows);
  const min = Math.min(...rows);
  const span = Math.max(...rows) - min || 1;
  const w = 120;
  const h = 26;
  const d = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * w;
      const y = h - ((p.rows - min) / span) * h;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={w} height={h} role="img" aria-label={t("telemetry.durable.trend")} style={{ overflow: "visible" }}>
      <path d={d} fill="none" stroke="var(--accent-h)" strokeWidth={1.5} />
    </svg>
  );
}

/** #407: the durable-store cost card — the numbers the "archive vs keep the
 * per-file model" call (#376) is made on. Always shown, even with no samples. */
function DurableStoreCard({ client }: { client: MonitorApi }) {
  const t = useT();
  const { data: s } = useQuery({
    queryKey: qk.monitorSummary,
    queryFn: () => client.getSummary(),
  });
  const dash = "—";
  const latestRows = s?.total_rows_trend.at(-1)?.rows;
  return (
    <section
      aria-label={t("telemetry.durable.aria")}
      style={{
        border: "1px solid var(--paper-3)",
        borderRadius: 8,
        padding: "12px 14px",
        margin: "6px 0 0",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <span style={{ fontSize: "var(--text-body-sm)", fontWeight: 600 }}>
          {t("telemetry.durable.title")}
        </span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: 20 }}>
        <Stat
          label={t("telemetry.durable.files")}
          value={s?.p95_n_files != null ? String(s.p95_n_files) : dash}
        />
        <Stat
          label={t("telemetry.durable.restore")}
          value={s?.p95_restore_ms != null ? `${s.p95_restore_ms} ms` : dash}
        />
        <Stat
          label={t("telemetry.durable.rows")}
          value={latestRows != null ? String(latestRows) : dash}
        />
        <Sparkline points={s?.total_rows_trend ?? []} />
      </div>
      <p style={{ margin: "10px 0 0", fontSize: pxToRem(11), color: "var(--text-paper-d2)" }}>
        {t("telemetry.durable.samples", {
          mirror: s?.n_mirror_samples ?? 0,
          restore: s?.n_restore_samples ?? 0,
        })}
      </p>
    </section>
  );
}

export function TelemetryPanel({ client = monitorApi }: { client?: MonitorApi }) {
  const t = useT();
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

  return (
    <>
      <DurableStoreCard client={client} />
      {traces.length === 0 ? (
        <p
          className="kb-cols__empty"
          role="status"
          style={{ marginTop: 24, color: "var(--text-paper-d)", fontSize: "var(--text-body-sm)" }}
        >
          {t("telemetry.empty")}
        </p>
      ) : (
        <ul style={{ listStyle: "none", margin: "18px 0 0", padding: 0 }}>
          {traces.map((tr) => (
            <TraceRow key={tr.traceId} trace={tr} />
          ))}
        </ul>
      )}
    </>
  );
}
