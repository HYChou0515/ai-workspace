/**
 * The capacity dashboard (docs/plan-capacity-usage.md §10, D4, D11): superusers
 * only. Answers, without anyone doing arithmetic, how much we use — of each
 * resource, by each person, in each place — and what more people would need.
 *
 * One analysis per period (7 / 28 / 90 days, E7), computed on the server on
 * demand and kept there for ten minutes; 重新計算 drops it. Five tabs:
 * 總覽｜分攤｜趨勢｜試算與時程｜門檻與可信度.
 */
import "../styles/capacity.css";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { qk } from "../api/queryKeys";
import {
  CAPACITY_DAYS,
  type CapacityClient,
  type CapacityDays,
  capacityApi,
  TooMuchData,
} from "../capacity/api";
import { Allocation, Segmented } from "../capacity/views/Allocation";
import { Overview } from "../capacity/views/Overview";
import { Thresholds } from "../capacity/views/Thresholds";
import { Trends } from "../capacity/views/Trends";
import { WhatIf } from "../capacity/views/WhatIf";
import { Tabs } from "../components/ShareTabs";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useIsSuperuserState } from "../hooks/useIsSuperuser";
import { useT } from "../lib/i18n";

type Tab = "overview" | "allocation" | "trends" | "whatif" | "thresholds";
const TABS: Tab[] = ["overview", "allocation", "trends", "whatif", "thresholds"];

export function CapacityPage({ client = capacityApi }: { client?: CapacityClient }) {
  const t = useT();
  useBreadcrumbs([{ label: t("nav.home"), to: "/" }, { label: t("capacity.title") }]);
  const { isSuperuser, ready } = useIsSuperuserState();
  const [days, setDays] = useState<CapacityDays>(28);
  const [tab, setTab] = useState<Tab>("overview");
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: qk.capacity(days),
    queryFn: () => client.summary(days),
    enabled: isSuperuser,
    retry: false,
    staleTime: 60_000,
    meta: { silentError: true },
  });
  const refresh = useMutation({
    meta: { silentError: true },
    mutationFn: () => client.refresh(days),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["capacity", days] }),
  });

  if (ready && !isSuperuser) {
    return (
      <div className="capacity-page" data-testid="page-capacity">
        <p className="capacity-notready" role="status">
          {t("capacity.forbidden")}
        </p>
      </div>
    );
  }

  const summary = query.data;
  const error = query.error;
  return (
    <div className="scrollable capacity-page" data-testid="page-capacity">
      <div className="capacity-wrap">
        <h1 className="capacity-h1">{t("capacity.title")}</h1>
        <p className="capacity-subtitle">{t("capacity.subtitle")}</p>
        <div className="capacity-controls">
          <Segmented
            label={t("capacity.period")}
            value={String(days) as `${CapacityDays}`}
            onChange={(v) => setDays(Number(v) as CapacityDays)}
            testId="capacity-period"
            options={CAPACITY_DAYS.map((d) => ({ value: String(d) as `${CapacityDays}`, label: t("capacity.days", { n: d }) }))}
          />
          <button
            type="button"
            className="btn"
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending || query.isFetching}
            data-testid="capacity-refresh"
          >
            {t("capacity.refresh")}
          </button>
          <span className="capacity-sub" aria-live="polite">
            {query.isFetching
              ? t("capacity.computing")
              : summary
                ? t("capacity.computed", { time: new Date(summary.period.generated_ms).toLocaleString() })
                : ""}
          </span>
        </div>
        {error && (
          <p role="alert" className="capacity-warning" data-testid="capacity-error">
            {error instanceof TooMuchData
              ? t("capacity.tooMuch", { rows: error.rows.toLocaleString(), max: error.maxRows.toLocaleString() })
              : t("capacity.loadFailed", { error: error.message })}
          </p>
        )}
        <div style={{ marginTop: 12 }}>
          <Tabs
            idPrefix="capacity"
            label={t("capacity.viewAria")}
            value={tab}
            onChange={(id) => setTab(id as Tab)}
            tabs={TABS.map((id) => ({ id, label: t(`capacity.tab.${id}`) }))}
          />
        </div>
        <div role="tabpanel" id={`capacity-panel-${tab}`} aria-labelledby={`capacity-tab-${tab}`}>
          {summary && tab === "overview" && <Overview summary={summary} t={t} />}
          {summary && tab === "allocation" && <Allocation summary={summary} days={days} client={client} t={t} />}
          {summary && tab === "trends" && <Trends summary={summary} t={t} />}
          {summary && tab === "whatif" && <WhatIf summary={summary} days={days} client={client} t={t} />}
          {summary && tab === "thresholds" && <Thresholds summary={summary} t={t} />}
        </div>
      </div>
    </div>
  );
}
