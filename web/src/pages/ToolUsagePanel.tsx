/**
 * The administrator's tool-usage tab (docs/plan-tool-usage-stats.md §3.7):
 * which tools the AI called, how often they failed, how long they took — per
 * tool, then one tool by person, by surface × App, by item.
 *
 * Guidance (cited in the plan):
 * - The window (7 / 30 / 90 days) is one choice among modes of the SAME
 *   content, so it is a segmented control, not tabs or separate buttons
 *   (Material 3, "Segmented buttons").
 * - The report is a data table the administrator scans, compares and acts on
 *   (NN/g, "Data Tables: Four Major User Tasks"): sortable columns, numbers
 *   right-aligned in one unit, filters above, and a row opens its breakdown.
 */
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  type ToolUsageClient,
  type ToolUsageTool,
  type UsageDays,
  toolUsageApi,
} from "../api/toolUsage";
import { qk } from "../api/queryKeys";
import { UserChip } from "../components/UserChip";
import { useApps } from "../hooks/useResources";
import { type MsgKey, useT } from "../lib/i18n";

const WINDOWS: UsageDays[] = [7, 30, 90];

const SOURCES: Record<string, MsgKey> = {
  builtin: "usage.source.builtin",
  first_party: "usage.source.first_party",
  third_party: "usage.source.third_party",
};

const SURFACES: Record<string, MsgKey> = {
  chat: "usage.surface.chat",
  kb_chat: "usage.surface.kb_chat",
  workflow: "usage.surface.workflow",
  schedule: "usage.surface.schedule",
  subagent: "usage.surface.subagent",
  kb_agent: "usage.surface.kb_agent",
  background: "usage.surface.background",
};

type SortKey = "tool" | "source" | "calls" | "fails" | "rate" | "avg_ms";

function rate(t: { calls: number; fails: number }): number {
  return t.calls ? t.fails / t.calls : 0;
}

const cell: React.CSSProperties = {
  padding: "6px 8px",
  borderBottom: "1px solid var(--paper-3)",
  textAlign: "left",
};
const num: React.CSSProperties = { ...cell, textAlign: "right", fontVariantNumeric: "tabular-nums" };

export function ToolUsagePanel({ client = toolUsageApi }: { client?: ToolUsageClient }) {
  const t = useT();
  const apps = useApps();
  const [days, setDays] = useState<UsageDays>(30);
  const [filter, setFilter] = useState({ source: "", surface: "", app: "" });
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "calls", desc: true });
  const [picked, setPicked] = useState<string | null>(null);

  const report = useQuery({
    queryKey: qk.toolUsage(days, filter),
    queryFn: () => client.report(days, filter),
  });

  const rows = useMemo(() => {
    const value = (r: ToolUsageTool): number | string =>
      sort.key === "rate" ? rate(r) : r[sort.key];
    return [...(report.data?.tools ?? [])].sort((a, b) => {
      const x = value(a);
      const y = value(b);
      const c = typeof x === "string" ? x.localeCompare(String(y)) : x - (y as number);
      return (sort.desc ? -c : c) || a.tool.localeCompare(b.tool);
    });
  }, [report.data, sort]);

  const ms = (n: number) =>
    n < 1000 ? t("usage.ms", { n }) : t("usage.seconds", { n: (n / 1000).toFixed(1) });
  const pct = (r: { calls: number; fails: number }) => `${Math.round(rate(r) * 100)}%`;

  const header = (key: SortKey, label: string, numeric = false) => {
    const on = sort.key === key;
    return (
      <th
        style={numeric ? num : cell}
        aria-sort={on ? (sort.desc ? "descending" : "ascending") : "none"}
      >
        <button
          type="button"
          className="btn"
          data-size="sm"
          data-variant="ghost"
          onClick={() => setSort(on ? { key, desc: !sort.desc } : { key, desc: true })}
        >
          {label}
          {on && <span aria-hidden="true">{sort.desc ? " ↓" : " ↑"}</span>}
        </button>
      </th>
    );
  };

  return (
    <div className="tool-usage" style={{ marginTop: 16 }}>
      <div className="tool-usage-controls">
        <div className="tool-usage-segmented" role="group" aria-label={t("usage.window")}>
          {WINDOWS.map((d) => (
            <button
              key={d}
              type="button"
              aria-pressed={d === days}
              onClick={() => setDays(d)}
            >
              {t("usage.days", { n: d })}
            </button>
          ))}
        </div>
        <label>
          {t("usage.filter.source")}
          <select
            className="input"
            value={filter.source}
            onChange={(e) => setFilter((f) => ({ ...f, source: e.target.value }))}
          >
            <option value="">{t("usage.all")}</option>
            {Object.entries(SOURCES).map(([id, key]) => (
              <option key={id} value={id}>
                {t(key)}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("usage.filter.surface")}
          <select
            className="input"
            value={filter.surface}
            onChange={(e) => setFilter((f) => ({ ...f, surface: e.target.value }))}
          >
            <option value="">{t("usage.all")}</option>
            {Object.entries(SURFACES).map(([id, key]) => (
              <option key={id} value={id}>
                {t(key)}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("usage.filter.app")}
          <select
            className="input"
            value={filter.app}
            onChange={(e) => setFilter((f) => ({ ...f, app: e.target.value }))}
          >
            <option value="">{t("usage.all")}</option>
            {apps.map((a) => (
              <option key={a.slug} value={a.slug}>
                {a.title}
              </option>
            ))}
          </select>
        </label>
      </div>

      {report.isError ? (
        <p className="tool-usage-note" role="alert">
          {t("usage.failed", { reason: (report.error as Error).message })}
        </p>
      ) : report.data && rows.length === 0 ? (
        <p className="tool-usage-note">{t("usage.empty")}</p>
      ) : (
        <div className="tool-usage-scroll">
          <table className="tool-usage-table">
            <thead>
              <tr>
                {header("tool", t("usage.col.tool"))}
                {header("source", t("usage.col.source"))}
                {header("calls", t("usage.col.calls"), true)}
                {header("fails", t("usage.col.fails"), true)}
                {header("rate", t("usage.col.rate"), true)}
                {header("avg_ms", t("usage.col.avg"), true)}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={`${r.tool}\u0000${r.source}`}
                  data-testid="tool-usage-row"
                  aria-selected={picked === r.tool}
                >
                  <td style={cell}>
                    <button
                      type="button"
                      className="tool-usage-tool"
                      onClick={() => setPicked(picked === r.tool ? null : r.tool)}
                    >
                      {r.tool}
                    </button>
                  </td>
                  <td style={cell}>{t(SOURCES[r.source] ?? "usage.source.builtin")}</td>
                  <td style={num}>{r.calls}</td>
                  <td style={num}>{r.fails}</td>
                  <td style={num}>{pct(r)}</td>
                  <td style={num}>{ms(r.avg_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {picked && (
        <ToolDetail
          tool={picked}
          days={days}
          client={client}
          ms={ms}
          pct={pct}
          appTitle={(slug) => apps.find((a) => a.slug === slug)?.title ?? slug}
        />
      )}
    </div>
  );
}

function ToolDetail({
  tool,
  days,
  client,
  ms,
  pct,
  appTitle,
}: {
  tool: string;
  days: UsageDays;
  client: ToolUsageClient;
  ms: (n: number) => string;
  pct: (r: { calls: number; fails: number }) => string;
  appTitle: (slug: string) => string;
}) {
  const t = useT();
  const detail = useQuery({
    queryKey: qk.toolUsageDetail(tool, days),
    queryFn: () => client.detail(tool, days),
  });
  const d = detail.data;
  const counts = (r: { calls: number; fails: number; avg_ms: number }) => (
    <>
      <td style={num}>{r.calls}</td>
      <td style={num}>{r.fails}</td>
      <td style={num}>{pct(r)}</td>
      <td style={num}>{ms(r.avg_ms)}</td>
    </>
  );
  const head = (first: string) => (
    <thead>
      <tr>
        <th style={cell}>{first}</th>
        <th style={num}>{t("usage.col.calls")}</th>
        <th style={num}>{t("usage.col.fails")}</th>
        <th style={num}>{t("usage.col.rate")}</th>
        <th style={num}>{t("usage.col.avg")}</th>
      </tr>
    </thead>
  );
  return (
    <section className="tool-usage-detail" data-testid="tool-usage-detail" aria-label={tool}>
      <h2 className="tool-usage-detail-title">{tool}</h2>
      {detail.isError && (
        <p role="alert">{t("usage.failed", { reason: (detail.error as Error).message })}</p>
      )}
      {d && (
        <>
          <h3>{t("usage.by.user")}</h3>
          <div className="tool-usage-scroll">
            <table className="tool-usage-table">
              {head(t("usage.col.user"))}
              <tbody>
                {d.by_user.map((r) => (
                  <tr key={r.user}>
                    <td style={cell}>
                      <UserChip userId={r.user} />
                    </td>
                    {counts(r)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>{t("usage.by.surface")}</h3>
          <div className="tool-usage-scroll">
            <table className="tool-usage-table">
              {head(t("usage.col.surface"))}
              <tbody>
                {d.by_surface_app.map((r) => (
                  <tr key={`${r.surface}\u0000${r.app}`}>
                    <td style={cell}>
                      {t(SURFACES[r.surface] ?? "usage.surface.background")}
                      {r.app ? ` · ${appTitle(r.app)}` : ""}
                    </td>
                    {counts(r)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>{t("usage.by.item")}</h3>
          <div className="tool-usage-scroll">
            <table className="tool-usage-table">
              {head(t("usage.col.item"))}
              <tbody>
                {d.by_item.map((r) => (
                  <tr key={r.item}>
                    <td style={cell}>
                      {r.slug && r.title ? (
                        <Link to={`/a/${encodeURIComponent(r.slug)}/${encodeURIComponent(r.item)}`}>
                          {r.title}
                        </Link>
                      ) : (
                        r.item || t("usage.noItem")
                      )}
                    </td>
                    {counts(r)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
