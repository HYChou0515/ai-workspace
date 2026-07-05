/**
 * Diagnostics page (#51 P5) — the global view of the AI capability
 * probes: what was checked, what the model couldn't do, and the manual
 * re-run triggers (Q2: startup + on-demand only, no scheduler).
 *
 * Q6: a failing probe is a WARNING — nothing here gates any feature.
 * The probe `description`s come from the backend registry; the page
 * adds only outcome labels, kept free of internal nouns.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { type HealthApi, type HealthCheckRow, healthApi } from "../api/health";
import { qk } from "../api/queryKeys";
import { Icon } from "../components/Icon";
import { type ChipTone } from "../components/StatusChip";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { SanityQuestions } from "./SanityQuestions";
import { SanityTable } from "./SanityTable";
import { SanityVerdicts } from "./SanityVerdicts";
import { TelemetryPanel } from "./TelemetryPanel";
import { pxToRem } from "../lib/pxToRem";

type Outcome = { label: string; tone: ChipTone };

function outcome(status: HealthCheckRow["status"]): Outcome {
  switch (status) {
    case "pass":
      return { label: "Normal", tone: "ok" };
    case "fail":
      return { label: "Issue found", tone: "warn" };
    case "error":
      return { label: "Couldn't run", tone: "err" };
    case "skip":
      return { label: "Not configured", tone: "muted" };
    default:
      return { label: "Not checked yet", tone: "muted" };
  }
}

const TONE_BG: Record<ChipTone, string> = {
  err: "rgba(196,74,58,.12)",
  warn: "rgba(198,138,46,.14)",
  ok: "rgba(58,138,74,.12)",
  info: "rgba(45,108,201,.12)",
  muted: "var(--paper-2)",
};

const TONE_FG: Record<ChipTone, string> = {
  err: "var(--err)",
  warn: "var(--warn)",
  ok: "var(--ok)",
  info: "var(--info)",
  muted: "var(--text-paper-d)",
};

function OutcomeChip({ status }: { status: HealthCheckRow["status"] }) {
  const o = outcome(status);
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "2px 9px",
        borderRadius: 999,
        fontSize: pxToRem(12),
        fontWeight: 600,
        whiteSpace: "nowrap",
        background: TONE_BG[o.tone],
        color: TONE_FG[o.tone],
      }}
    >
      {o.label}
    </span>
  );
}

function when(ms: number | null): string {
  if (!ms) return "";
  return new Date(ms).toLocaleString();
}

export function DiagnosticsPage({ client = healthApi }: { client?: HealthApi }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  useBreadcrumbs([{ label: "Home", to: "/" }, { label: "Diagnostics" }]);
  const [tab, setTab] = useState<"checks" | "traces" | "matrix">("checks");
  const { data } = useQuery({
    queryKey: qk.health,
    queryFn: () => client.getChecks(),
    // Poll fast while a round is in flight so outcomes land live.
    refetchInterval: (query) => (query.state.data?.running ? 1200 : false),
  });

  const run = useMutation({
    mutationFn: (checkId?: string) =>
      checkId === undefined ? client.runChecks() : client.runChecks(checkId),
    onSettled: () => queryClient.invalidateQueries({ queryKey: qk.health }),
  });

  const running = data?.running ?? false;
  const checks = data?.checks ?? [];

  return (
    <div
      data-testid="page-diagnostics"
      style={{ minHeight: "100%", background: "var(--paper)", overflow: "auto" }}
    >
      <div
        style={{
          maxWidth: tab === "matrix" ? 1180 : 760,
          margin: "0 auto",
          padding: "28px 20px 60px",
        }}
      >
        <button
          type="button"
          onClick={() => navigate(-1)}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            border: "none",
            background: "none",
            color: "var(--text-paper-d)",
            fontSize: "var(--text-body-sm)",
            cursor: "pointer",
            padding: 0,
            marginBottom: 18,
          }}
        >
          <Icon name="chev_l" size={13} /> Back
        </button>

        <header
          style={{
            display: "flex",
            alignItems: "flex-end",
            justifyContent: "space-between",
            gap: 12,
            marginBottom: 6,
          }}
        >
          <div>
            <h1 style={{ margin: 0, fontSize: pxToRem(22) }}>AI diagnostics</h1>
            <p
              style={{
                margin: "6px 0 0",
                color: "var(--text-paper-d)",
                fontSize: "var(--text-body-sm)",
                maxWidth: 480,
              }}
            >
              Quick checks that verify the AI features behind this workspace are responding
              and doing their jobs. A warning here never blocks you — it explains why
              something might look off.
            </p>
          </div>
          {tab === "checks" && (
            <button
              type="button"
              disabled={running}
              onClick={() => run.mutate(undefined)}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "7px 14px",
                borderRadius: "var(--radius-btn)",
                border: "1px solid var(--paper-3)",
                background: "var(--paper-2)",
                cursor: running ? "default" : "pointer",
                fontSize: "var(--text-body-sm)",
                fontWeight: 600,
                opacity: running ? 0.6 : 1,
                whiteSpace: "nowrap",
              }}
            >
              <Icon name="refresh" size={14} /> Run all checks
            </button>
          )}
        </header>

        <div
          className="kb-tabs"
          role="tablist"
          aria-label="Diagnostics view"
          style={{ display: "flex", gap: 4, margin: "16px 0 0" }}
        >
          {(
            [
              ["checks", "Health checks"],
              ["matrix", "Model sanity"],
              // #171: "Activity" (not the OTel jargon "Traces") on the tab; the
              // route/state key stays "traces".
              ["traces", "Activity"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              className={`kb-tab${tab === id ? " is-active" : ""}`}
              onClick={() => setTab(id)}
              style={{
                padding: "6px 12px",
                border: "1px solid var(--paper-3)",
                borderRadius: "var(--radius-btn)",
                background: tab === id ? "var(--accent-soft)" : "transparent",
                color: tab === id ? "var(--accent-h)" : "var(--text-paper-d)",
                fontSize: "var(--text-body-sm)",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {label}
            </button>
          ))}
        </div>

        {tab === "traces" && <TelemetryPanel />}

        {tab === "matrix" && (
          <>
            <SanityVerdicts />
            <SanityTable />
            <SanityQuestions />
          </>
        )}

        {tab === "checks" && running && (
          <div
            role="status"
            style={{
              margin: "14px 0 0",
              padding: "8px 12px",
              borderRadius: "var(--radius-btn)",
              background: "rgba(45,108,201,.08)",
              color: "var(--info)",
              fontSize: "var(--text-body-sm)",
            }}
          >
            Checking… results update as each probe finishes.
          </div>
        )}

        {tab === "checks" && (
        <ul style={{ listStyle: "none", margin: "18px 0 0", padding: 0 }}>
          {checks.map((c) => (
            <li
              key={c.check_id}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 12,
                padding: "13px 14px",
                borderBottom: "1px solid var(--paper-3)",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: "var(--text-body)", fontWeight: 500 }}>
                  {c.description}
                </div>
                {c.detail && c.status !== "pass" && (
                  <div
                    style={{
                      marginTop: 4,
                      fontSize: "var(--text-body-sm)",
                      color: c.status === "skip" ? "var(--text-paper-d)" : "var(--warn)",
                      overflowWrap: "anywhere",
                    }}
                  >
                    {c.detail}
                  </div>
                )}
                {c.checked_at != null && (
                  <div
                    style={{
                      marginTop: 4,
                      fontSize: pxToRem(11),
                      color: "var(--text-paper-d)",
                    }}
                  >
                    Last checked {when(c.checked_at)}
                    {c.latency_ms != null ? ` · took ${(c.latency_ms / 1000).toFixed(1)}s` : ""}
                  </div>
                )}
              </div>
              <OutcomeChip status={c.status} />
              <button
                type="button"
                disabled={running}
                aria-label={`Re-run: ${c.description}`}
                title="Re-run this check"
                onClick={() => run.mutate(c.check_id)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  border: "1px solid var(--paper-3)",
                  background: "none",
                  borderRadius: 6,
                  padding: 5,
                  cursor: running ? "default" : "pointer",
                  color: "var(--text-paper-d)",
                  opacity: running ? 0.5 : 1,
                }}
              >
                <Icon name="refresh" size={13} />
              </button>
            </li>
          ))}
        </ul>
        )}
      </div>
    </div>
  );
}
