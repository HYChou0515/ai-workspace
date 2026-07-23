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
import { type MsgKey, useT } from "../lib/i18n";
import { RetrievalEvalPanel } from "./RetrievalEvalPanel";
import { SanityQuestions } from "./SanityQuestions";
import { SanityTable } from "./SanityTable";
import { SanityVerdicts } from "./SanityVerdicts";
import { TelemetryPanel } from "./TelemetryPanel";
import { pxToRem } from "../lib/pxToRem";

type Outcome = { key: MsgKey; tone: ChipTone };

function outcome(status: HealthCheckRow["status"]): Outcome {
  switch (status) {
    case "pass":
      return { key: "diag.outcome.pass", tone: "ok" };
    case "fail":
      return { key: "diag.outcome.fail", tone: "warn" };
    case "error":
      return { key: "diag.outcome.error", tone: "err" };
    case "skip":
      return { key: "diag.outcome.skip", tone: "muted" };
    default:
      return { key: "diag.outcome.none", tone: "muted" };
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
  const t = useT();
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
      {t(o.key)}
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
  const t = useT();
  useBreadcrumbs([{ label: t("nav.home"), to: "/" }, { label: t("diag.crumb") }]);
  const [tab, setTab] = useState<"checks" | "traces" | "matrix" | "retrieval">("checks");
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
          maxWidth: tab === "matrix" || tab === "retrieval" ? 1180 : 760,
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
          <Icon name="chev_l" size={13} /> {t("diag.back")}
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
            <h1 style={{ margin: 0, fontSize: pxToRem(22) }}>{t("diag.title")}</h1>
            <p
              style={{
                margin: "6px 0 0",
                color: "var(--text-paper-d)",
                fontSize: "var(--text-body-sm)",
                maxWidth: 480,
              }}
            >
              {t("diag.subtitle")}
            </p>
          </div>
          {tab === "retrieval" && <RetrievalEvalPanel />}
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
              <Icon name="refresh" size={14} /> {t("diag.runAll")}
            </button>
          )}
        </header>

        <div
          className="kb-tabs"
          role="tablist"
          aria-label={t("diag.viewAria")}
          style={{ display: "flex", gap: 4, margin: "16px 0 0" }}
        >
          {(
            [
              ["checks", t("diag.tab.checks")],
              ["matrix", t("diag.tab.matrix")],
              // #171: "Activity" (not the OTel jargon "Traces") on the tab; the
              // route/state key stays "traces".
              ["traces", t("diag.tab.activity")],
              // #535: the retrieval-eval face — fire a pass, read recall@k/MRR.
              ["retrieval", t("diag.tab.retrieval")],
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
            {t("diag.checking")}
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
                    {t("diag.lastChecked", { when: when(c.checked_at) })}
                    {c.latency_ms != null
                      ? t("diag.took", { sec: (c.latency_ms / 1000).toFixed(1) })
                      : ""}
                  </div>
                )}
              </div>
              <OutcomeChip status={c.status} />
              <button
                type="button"
                disabled={running}
                aria-label={t("diag.rerunAria", { name: c.description })}
                title={t("diag.rerunTitle")}
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
