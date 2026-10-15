/**
 * Views 2–4 — 分攤 (docs/plan-capacity-usage.md §10, D11): who used how much of
 * each resource, where; one person's detail; how different people are.
 *
 * Fixed overhead is a toggle, as Kubecost and OpenCost draw it: its own row
 * (the default), spread by use, or spread evenly — the same numbers three ways.
 * "Peak hours" is each line's busiest 5% of slots (E8): what machines are
 * bought for.
 */
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { qk } from "../../api/queryKeys";
import { useUsers } from "../../hooks/useUsers";
import type { CapacityClient, CapacityDays, CapacitySummary, Line } from "../api";
import { Chart } from "../Chart";
import type { ChartOption } from "../echarts";
import {
  dayLabel,
  formatValue,
  lineLabel,
  NOT_PEOPLE,
  pct,
  personLabel,
  purposeLabel,
  type T,
} from "../format";
import { NotReady } from "../Readiness";

export type GroupBy = "person" | "component" | "app" | "purpose";
export type Stat = "avg" | "peak";
export type FixedMode = "separate" | "proportional" | "even";

export type Row = { key: string; label: string; value: number; kind: "group" | "fixed" | "residual" | "other" };

/** The table for one line, grouping, statistic and fixed-overhead mode. */
export function allocationRows(
  t: T,
  summary: CapacitySummary,
  line: Line,
  by: GroupBy,
  stat: Stat,
  mode: FixedMode,
  name: (id: string) => string,
): Row[] {
  if (by === "component") {
    return summary.lines
      .filter((l) => l.resource === line.resource && l.unit === line.unit && l.total !== null)
      .map((l) => ({ key: l.id, label: lineLabel(t, l), value: l.total?.[stat] ?? 0, kind: "group" as const }))
      .sort((a, b) => b.value - a.value);
  }
  const groups = line.groups[by];
  const label = (key: string) =>
    by === "person" ? personLabel(t, key, name) : by === "purpose" ? purposeLabel(t, key) : key;
  let rows: Row[] = groups.map((g) => ({ key: g.key, label: label(g.key), value: g[stat], kind: "group" }));
  const fixed = line.fixed?.[stat] ?? 0;
  if (mode === "separate" || fixed === 0) {
    if (fixed) rows.push({ key: "__fixed", label: t("capacity.row.fixed"), value: fixed, kind: "fixed" });
  } else if (mode === "proportional") {
    const whole = rows.reduce((sum, r) => sum + r.value, 0);
    rows = rows.map((r) => ({ ...r, value: whole ? r.value + (fixed * r.value) / whole : r.value }));
  } else {
    const shares = rows.filter((r) => by !== "person" || !NOT_PEOPLE.has(r.key));
    const each = shares.length ? fixed / shares.length : 0;
    rows = rows.map((r) => (shares.includes(r) ? { ...r, value: r.value + each } : r));
  }
  const other = line.other?.[stat] ?? 0;
  if (other) rows.push({ key: "__other", label: t("capacity.row.other"), value: other, kind: "other" });
  const residual = line.residual?.[stat] ?? 0;
  if (Math.abs(residual) > 1e-9) {
    rows.push({ key: "__residual", label: t("capacity.row.residual"), value: residual, kind: "residual" });
  }
  return rows.sort((a, b) => (a.kind === "group") === (b.kind === "group") ? b.value - a.value : a.kind === "group" ? -1 : 1);
}

function Segmented<V extends string>({
  label,
  value,
  options,
  onChange,
  testId,
}: {
  label: string;
  value: V;
  options: { value: V; label: string; disabled?: boolean }[];
  onChange: (v: V) => void;
  testId: string;
}) {
  return (
    <div className="capacity-segmented" role="group" aria-label={label} data-testid={testId}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={o.value === value}
          disabled={o.disabled}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export { Segmented };

export function Allocation({
  summary,
  days,
  client,
  t,
}: {
  summary: CapacitySummary;
  days: CapacityDays;
  client: CapacityClient;
  t: T;
}) {
  const users = useUsers();
  const name = (id: string) => users.find((u) => u.id === id)?.name ?? id;
  const lines = summary.lines.filter((l) => l.total !== null);
  const [lineId, setLineId] = useState(lines[0]?.id ?? "");
  const [by, setBy] = useState<GroupBy>("person");
  const [stat, setStat] = useState<Stat>("peak");
  const [mode, setMode] = useState<FixedMode>("separate");
  const [person, setPerson] = useState<string | null>(null);
  const line = lines.find((l) => l.id === lineId) ?? lines[0];
  const rows = useMemo(
    () => (line ? allocationRows(t, summary, line, by, stat, mode, name) : []),
    // `name` reads the user list, which `users` stands for.
    [t, summary, line, by, stat, mode, users],
  );
  const total = rows.reduce((sum, r) => sum + r.value, 0);
  const option = useMemo<ChartOption>(() => barChart(rows.slice(0, 15), line?.unit === "bytes"), [rows, line]);

  return (
    <section className="capacity-view" data-testid="capacity-allocation">
      <NotReady readiness={summary.readiness.allocation} t={t} />
      {line && (
        <>
          <div className="capacity-controls">
            <label>
              {t("capacity.alloc.resource")}
              <select
                className="input"
                value={line.id}
                onChange={(e) => setLineId(e.target.value)}
                data-testid="capacity-alloc-line"
              >
                {lines.map((l) => (
                  <option key={l.id} value={l.id}>
                    {lineLabel(t, l)}
                  </option>
                ))}
              </select>
            </label>
            <Segmented
              label={t("capacity.alloc.by")}
              value={by}
              onChange={setBy}
              testId="capacity-alloc-by"
              options={(["person", "component", "app", "purpose"] as const).map((v) => ({
                value: v,
                label: t(`capacity.alloc.by.${v}`),
              }))}
            />
            <Segmented
              label={t("capacity.alloc.stat")}
              value={stat}
              onChange={setStat}
              testId="capacity-alloc-stat"
              options={(["peak", "avg"] as const).map((v) => ({ value: v, label: t(`capacity.alloc.stat.${v}`) }))}
            />
            <Segmented
              label={t("capacity.alloc.fixed")}
              value={mode}
              onChange={setMode}
              testId="capacity-alloc-fixed"
              options={(["separate", "proportional", "even"] as const).map((v) => ({
                value: v,
                label: t(`capacity.alloc.fixed.${v}`),
                disabled: by === "component",
              }))}
            />
          </div>
          {stat === "peak" && <p className="capacity-note">{t("capacity.alloc.peakNote")}</p>}
          <Chart option={option} label={lineLabel(t, line)} height={Math.max(160, 26 * Math.min(rows.length, 15) + 50)} testId="capacity-alloc-chart" />
          <div className="capacity-scroll">
            <table className="capacity-table" data-testid="capacity-alloc-table">
              <thead>
                <tr>
                  <th scope="col">{t(`capacity.alloc.by.${by}`)}</th>
                  <th scope="col">{t("capacity.alloc.use")}</th>
                  <th scope="col">{t("capacity.alloc.share")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} data-kind={r.kind} aria-selected={person === r.key || undefined}>
                    <th scope="row">
                      {by === "person" && r.kind === "group" && !NOT_PEOPLE.has(r.key) ? (
                        <button type="button" className="capacity-link" onClick={() => setPerson(r.key)}>
                          {r.label}
                        </button>
                      ) : (
                        r.label
                      )}
                    </th>
                    <td>{formatValue(t, r.value, line.unit)}</td>
                    <td>{pct(total ? r.value / total : null, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {person && (
            <PersonDetail
              user={person}
              label={name(person)}
              days={days}
              client={client}
              summary={summary}
              t={t}
              onClose={() => setPerson(null)}
            />
          )}
          <Distribution summary={summary} line={line} t={t} />
        </>
      )}
    </section>
  );
}

function barChart(rows: Row[], isBytes: boolean): ChartOption {
  return {
    animation: false,
    grid: { left: 8, right: 24, top: 8, bottom: 24, containLabel: true },
    tooltip: { trigger: "axis" },
    xAxis: { type: "value", axisLabel: isBytes ? { formatter: (v: number) => `${(v / 1024 ** 3).toFixed(1)}G` } : {} },
    yAxis: { type: "category", data: rows.map((r) => r.label), inverse: true },
    series: [
      {
        type: "bar",
        data: rows.map((r) => ({
          value: r.value,
          itemStyle: r.kind === "group" ? undefined : { opacity: 0.45 },
        })),
        barMaxWidth: 14,
      },
    ],
  };
}

/** View 3 — one person: their lines over time, Apps, items, rank. */
function PersonDetail({
  user,
  label,
  days,
  client,
  summary,
  t,
  onClose,
}: {
  user: string;
  label: string;
  days: CapacityDays;
  client: CapacityClient;
  summary: CapacitySummary;
  t: T;
  onClose: () => void;
}) {
  const { data } = useQuery({
    queryKey: qk.capacityPerson(user, days),
    queryFn: () => client.person(user, days),
  });
  const option = useMemo<ChartOption | null>(() => {
    if (!data) return null;
    const shown = data.lines.filter((l) => l.unit === "cores");
    return {
      animation: false,
      grid: { left: 8, right: 16, top: 28, bottom: 24, containLabel: true },
      legend: { top: 0 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: summary.period.days.map(dayLabel) },
      yAxis: { type: "value" },
      series: shown.map((l) => ({
        type: "line",
        name: lineLabel(t, summary.lines.find((x) => x.id === l.id) ?? { component: l.id, resource: "", model: "" }),
        data: l.daily_peak,
        showSymbol: false,
      })),
    };
  }, [data, summary, t]);
  return (
    <aside className="capacity-person" data-testid="capacity-person" aria-label={t("capacity.person.title", { user: label })}>
      <header>
        <h3 className="capacity-h3">{t("capacity.person.title", { user: label })}</h3>
        <button type="button" className="capacity-link" onClick={onClose}>
          {t("capacity.person.close")}
        </button>
      </header>
      {data && (
        <>
          <p className="capacity-note">
            {t("capacity.person.active", { n: data.active_days, turns: data.turns, jobs: data.jobs })}
          </p>
          <ul className="capacity-ranks">
            {data.lines.map((l) => {
              const line = summary.lines.find((x) => x.id === l.id);
              return (
                <li key={l.id}>
                  <span>{line ? lineLabel(t, line) : l.id}</span>
                  <span>{formatValue(t, l.avg, l.unit)}</span>
                  <span className="capacity-sub">{t("capacity.person.rank", { pct: pct(l.rank) })}</span>
                </li>
              );
            })}
          </ul>
          {option && <Chart option={option} label={t("capacity.person.title", { user: label })} height={200} />}
          {data.apps.length > 0 && (
            <p className="capacity-note">
              {t("capacity.person.apps")}：{data.apps.join("、")}
            </p>
          )}
          {data.items.length > 0 && (
            <table className="capacity-table">
              <thead>
                <tr>
                  <th scope="col">{t("capacity.person.items")}</th>
                  <th scope="col">{t("capacity.person.sandbox")}</th>
                  <th scope="col">{t("capacity.person.disk")}</th>
                  <th scope="col">{t("capacity.person.turns")}</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id}>
                    <th scope="row">{item.id}</th>
                    <td>{item.sandbox_cpu_s == null ? "—" : Math.round(item.sandbox_cpu_s).toLocaleString()}</td>
                    <td>{item.disk_bytes == null ? "—" : formatValue(t, item.disk_bytes, "bytes")}</td>
                    <td>{item.turns ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </aside>
  );
}

/** View 4 — how different people are, in peak hours. */
function Distribution({ summary, line, t }: { summary: CapacitySummary; line: Line; t: T }) {
  const dist = line.distribution;
  const option = useMemo<ChartOption | null>(() => {
    if (!dist || dist.values.length === 0) return null;
    const max = Math.max(...dist.values);
    const bins = 12;
    const width = max / bins || 1;
    const counts = new Array(bins).fill(0) as number[];
    for (const v of dist.values) counts[Math.min(bins - 1, Math.floor(v / width))] += 1;
    return {
      animation: false,
      grid: { left: 8, right: 16, top: 8, bottom: 24, containLabel: true },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: counts.map((_, i) => formatValue(t, (i + 0.5) * width, line.unit)),
      },
      yAxis: { type: "value", minInterval: 1 },
      series: [{ type: "bar", data: counts, barCategoryGap: "8%" }],
    };
  }, [dist, line, t]);
  return (
    <section className="capacity-sub-view" data-testid="capacity-distribution">
      <h3 className="capacity-h3">{t("capacity.dist.title")}</h3>
      <NotReady readiness={summary.readiness.distribution} t={t} />
      {summary.readiness.distribution.ready && dist && (
        <>
          <p className="capacity-note">
            {t("capacity.dist.summary", {
              p50: formatValue(t, dist.p50, line.unit),
              p90: formatValue(t, dist.p90, line.unit),
              p99: formatValue(t, dist.p99, line.unit),
              top: pct(dist.top10_share),
            })}
          </p>
          {option && <Chart option={option} label={t("capacity.dist.title")} height={200} />}
        </>
      )}
    </section>
  );
}
