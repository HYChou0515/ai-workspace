/**
 * Views 2–4 — 分攤 (docs/plan-capacity-usage.md §10, D11): who used how much of
 * each resource, where; one person's detail; how different people are.
 *
 * Fixed overhead is a toggle, as Kubecost and OpenCost draw it: its own row
 * (the default), spread by use, or spread evenly over today's N, the people
 * active in the last 30 days (§9.4) — or over the people who used the line in
 * the period, when they are more (a 90-day period) — the same whole three
 * ways; each bar stacks a group's own use and the overhead it carries. "Peak hours" is each line's busiest 5%
 * of slots (E8): what machines are bought for.
 */
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { qk } from "../../api/queryKeys";
import { useUsers } from "../../hooks/useUsers";
import type { CapacityClient, CapacityDays, CapacitySummary, Line, Unit } from "../api";
import { Chart } from "../Chart";
import type { ChartOption } from "../echarts";
import {
  dayLabel,
  formatValue,
  lineLabel,
  list,
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

/** `value` = `own` + `carried`: a group's own use and the fixed overhead it carries. */
export type Row = {
  key: string;
  label: string;
  value: number;
  own: number;
  carried: number;
  kind: "group" | "fixed" | "residual" | "other";
};

/** Whether `mode` applies to `by`: evenly is per person (§9.4), and an App or
 * a purpose is not a head to count. */
export function modeApplies(by: GroupBy, mode: FixedMode): boolean {
  return by !== "component" && (mode !== "even" || by === "person");
}

/** The table for one line, grouping, statistic and fixed-overhead mode. Every
 * mode adds up to the same whole. */
export function allocationRows(
  t: T,
  summary: CapacitySummary,
  line: Line,
  by: GroupBy,
  stat: Stat,
  mode: FixedMode,
  name: (id: string) => string,
): Row[] {
  const row = (key: string, label: string, own: number, kind: Row["kind"], carried = 0): Row => ({
    key,
    label,
    own,
    carried,
    value: own + carried,
    kind,
  });
  if (by === "component") {
    return summary.lines
      .filter((l) => l.resource === line.resource && l.unit === line.unit && l.total !== null)
      .map((l) => row(l.id, lineLabel(t, l), l.total?.[stat] ?? 0, "group"))
      .sort((a, b) => b.value - a.value);
  }
  const label = (key: string) =>
    by === "person"
      ? personLabel(t, key, name)
      : by === "purpose"
        ? purposeLabel(t, key)
        : key === "(none)"
          ? t("capacity.app.none")
          : key;
  let rows: Row[] = line.groups[by].map((g) => row(g.key, label(g.key), g[stat], "group"));
  const fixed = line.fixed?.[stat] ?? 0;
  const spread = modeApplies(by, mode) ? mode : "separate";
  const whole = rows.reduce((sum, r) => sum + r.own, 0);
  if (fixed && spread === "proportional" && whole) {
    rows = rows.map((r) => row(r.key, r.label, r.own, r.kind, (fixed * r.own) / whole));
  } else if (fixed && spread === "even") {
    // over the people active in the last 30 days: those with no use of this
    // line carry their part too, in a row of their own
    const people = rows.filter((r) => !NOT_PEOPLE.has(r.key));
    const heads = Math.max(summary.people.n_now, people.length);
    const each = heads ? fixed / heads : 0;
    rows = rows.map((r) => (people.includes(r) ? row(r.key, r.label, r.own, r.kind, each) : r));
    const rest = heads - people.length;
    if (rest > 0) rows.push(row("__rest", t("capacity.row.restOfPeople", { n: rest }), 0, "fixed", each * rest));
    if (!heads) rows.push(row("__fixed", t("capacity.row.fixed"), fixed, "fixed"));
  } else if (fixed) {
    rows.push(row("__fixed", t("capacity.row.fixed"), fixed, "fixed"));
  }
  const other = line.other?.[stat] ?? 0;
  if (other) rows.push(row("__other", t("capacity.row.other"), other, "other"));
  const residual = line.residual?.[stat] ?? 0;
  if (Math.abs(residual) > 1e-9) rows.push(row("__residual", t("capacity.row.residual"), residual, "residual"));
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
  const option = useMemo<ChartOption>(() => barChart(t, rows.slice(0, 15), line?.unit ?? "cores"), [t, rows, line]);
  // a split by regression is shown once three days of both sources fit it (§9.10)
  const splitting = line?.method === "regression" && !summary.readiness.regression.ready;

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
                disabled: !modeApplies(by, v),
              }))}
            />
          </div>
          {stat === "peak" && <p className="capacity-note">{t("capacity.alloc.peakNote")}</p>}
          {splitting && <NotReady readiness={summary.readiness.regression} t={t} />}
          {!splitting && <Chart option={option} label={lineLabel(t, line)} height={Math.max(160, 26 * Math.min(rows.length, 15) + 50)} testId="capacity-alloc-chart" />}
          {!splitting && <div className="capacity-scroll">            <table className="capacity-table" data-testid="capacity-alloc-table">
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
          </div>}
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

/** One bar per row, stacked: its own use, and the fixed overhead it carries. */
export function barChart(t: T, rows: Row[], unit: Unit): ChartOption {
  const isBytes = unit === "bytes";
  const carrying = rows.some((r) => r.carried > 0);
  const own = {
    type: "bar" as const,
    name: t("capacity.alloc.use"),
    stack: "row",
    data: rows.map((r) => ({ value: r.own, itemStyle: r.kind === "group" ? undefined : { opacity: 0.45 } })),
    barMaxWidth: 14,
  };
  const carried = {
    type: "bar" as const,
    name: t("capacity.alloc.carried"),
    stack: "row",
    data: rows.map((r) => ({ value: r.carried, itemStyle: { opacity: 0.45 } })),
    barMaxWidth: 14,
  };
  return {
    animation: false,
    grid: { left: 8, right: 24, top: carrying ? 28 : 8, bottom: 24, containLabel: true },
    legend: carrying ? { top: 0 } : undefined,
    tooltip: { trigger: "axis", valueFormatter: (v: unknown) => formatValue(t, Number(v), unit) },
    xAxis: { type: "value", axisLabel: isBytes ? { formatter: (v: number) => `${(v / 1024 ** 3).toFixed(1)}G` } : {} },
    yAxis: { type: "category", data: rows.map((r) => r.label), inverse: true },
    series: carrying ? [own, carried] : [own],
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
  // a line split by regression is held back here as everywhere until three
  // days of both sources fit it (§9.10)
  const held = (id: string) =>
    !summary.readiness.regression.ready && summary.lines.find((x) => x.id === id)?.method === "regression";
  const lines = useMemo(() => (data ? data.lines.filter((l) => !held(l.id)) : []), [data, summary]);
  // one chart per unit: cores, bytes and requests in flight share no axis
  const charts = useMemo<{ unit: Unit; option: ChartOption }[]>(() => {
    if (!data) return [];
    const units: Unit[] = ["cores", "bytes", "inflight"];
    return units
      .map((unit) => ({ unit, shown: lines.filter((l) => l.unit === unit) }))
      .filter(({ shown }) => shown.length > 0)
      .map(({ unit, shown }) => ({
        unit,
        option: {
          animation: false,
          grid: { left: 8, right: 16, top: 28, bottom: 24, containLabel: true },
          legend: { top: 0 },
          tooltip: { trigger: "axis", valueFormatter: (v: unknown) => formatValue(t, Number(v), unit) },
          xAxis: { type: "category", data: summary.period.days.map(dayLabel) },
          yAxis: { type: "value", axisLabel: { formatter: (v: number) => formatValue(t, v, unit) } },
          series: shown.map((l) => ({
            type: "line",
            name: lineLabel(t, summary.lines.find((x) => x.id === l.id) ?? { component: l.id, resource: "", model: "" }),
            data: l.daily_peak,
            showSymbol: false,
          })),
        },
      }));
  }, [data, lines, summary, t]);
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
            {lines.map((l) => {
              const line = summary.lines.find((x) => x.id === l.id);
              return (
                <li key={l.id}>
                  <span>{line ? lineLabel(t, line) : l.id}</span>
                  <span>{formatValue(t, l.avg, l.unit)}</span>
                  {l.rank != null && (
                    <span className="capacity-sub">{t("capacity.person.rank", { pct: pct(l.rank) })}</span>
                  )}
                </li>
              );
            })}
          </ul>
          {charts.map(({ unit, option }) => (
            <Chart key={unit} option={option} label={t("capacity.person.title", { user: label })} height={200} />
          ))}
          {data.apps.length > 0 && <p className="capacity-note">{t("capacity.person.apps", { apps: list(t, data.apps) })}</p>}
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
  // a line split by regression waits for three days of both sources (§9.10)
  const splitting = line.method === "regression" && !summary.readiness.regression.ready;
  return (
    <section className="capacity-sub-view" data-testid="capacity-distribution">
      <h3 className="capacity-h3">{t("capacity.dist.title")}</h3>
      <NotReady readiness={summary.readiness.distribution} t={t} />
      {summary.readiness.distribution.ready && splitting && (
        <NotReady readiness={summary.readiness.regression} t={t} />
      )}
      {summary.readiness.distribution.ready && !splitting && dist && (
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
