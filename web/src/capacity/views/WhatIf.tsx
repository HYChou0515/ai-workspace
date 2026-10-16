/**
 * Views 6–7 — 試算與時程 (docs/plan-capacity-usage.md §10): what N people would
 * need, whose habits they bring, and — at the current growth — when the limit
 * arrives, within an 80% band (§9.7), and by when to order, given how long a
 * purchase takes (E12): by the band's early end.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { qk } from "../../api/queryKeys";
import type { CapacityClient, CapacityDays, CapacitySummary, WhatIfProfile } from "../api";
import { Chart } from "../Chart";
import type { ChartOption } from "../echarts";
import { bytes, dayLabel, formatValue, lineLabelById, machineCount, pct, type T } from "../format";
import { NotReady } from "../Readiness";
import { Segmented } from "./Allocation";
import { PeopleCaveat, recordedDays } from "./Overview";

export type Level = "p50" | "p95";
/** E12: until someone types theirs. */
export const DEFAULT_LEAD_DAYS = 90;
/** The latest instant a JS Date holds (±8.64e15 ms): a date past it is no date. */
const LAST_DATE_MS = 8.64e15;

export function advisory(summary: CapacitySummary): number | null {
  const bt = summary.confidence.backtest;
  return bt && bt.mape > summary.confidence.mape_ok ? bt.mape : null;
}

/** When the people trend reaches the headroom — with the 80% band's early
 * and late ends, worked out by the analysis (the band widens the further
 * ahead it looks; its slow end may never get there) — or what stops it: no
 * limit, no growth, already past, or a headroom that is only "at least" (the
 * what-if looked no further), which has no date to order by. */
export function runwayDate(
  summary: CapacitySummary,
):
  | { kind: "flat" }
  | { kind: "past" }
  | { kind: "none" }
  | { kind: "beyond"; atMs: number; slope: number; target: number }
  | { kind: "date"; atMs: number; earlyMs: number; lateMs: number | null; slope: number; target: number } {
  const { trend, reach } = summary.runway;
  const room = summary.headroom;
  if (!trend || !room || room.max_n === null) return { kind: "none" };
  if (room.max_n < room.n_now) return { kind: "past" };
  const ms = (days: number | null | undefined) => {
    if (days === null || days === undefined) return null;
    const at = summary.period.end_ms + days * 86_400_000;
    return Number.isFinite(at) && at <= LAST_DATE_MS ? at : null;
  };
  const atMs = ms(reach?.at);
  if (trend.slope <= 0 || !reach || atMs === null) return { kind: "flat" };
  if (room.ceiling) return { kind: "beyond", atMs, slope: trend.slope, target: room.max_n };
  return {
    kind: "date",
    atMs,
    // the band's early end is never after the line's crossing
    earlyMs: ms(reach.early) ?? atMs,
    lateMs: ms(reach.late),
    slope: trend.slope,
    target: room.max_n,
  };
}

function dateText(ms: number): string {
  return new Date(ms).toLocaleDateString();
}

/** "Order by …" — or, when that day is already behind today, that it has passed. */
function orderText(t: T, orderMs: number, todayMs: number): string {
  return orderMs < todayMs
    ? t("capacity.runway.orderPast", { order: dateText(orderMs) })
    : t("capacity.runway.order", { order: dateText(orderMs) });
}

export function WhatIf({
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
  const ready = summary.readiness.whatif.ready;
  const nNow = summary.people.n_now;
  const apps = useMemo(
    () =>
      [...new Set(summary.lines.flatMap((l) => l.groups.app.map((g) => g.key)))].filter((a) => a && a !== "(none)").sort(),
    [summary],
  );
  const [draft, setDraft] = useState(String(Math.max(1, nNow * 2)));
  const [n, setN] = useState(Math.max(1, nNow * 2));
  const [profile, setProfile] = useState<WhatIfProfile>("all");
  const [app, setApp] = useState(apps[0] ?? "");
  const [level, setLevel] = useState<Level>("p95");
  const { data } = useQuery({
    queryKey: qk.capacityWhatIf(days, n, profile, profile === "app" ? app : ""),
    queryFn: () => client.whatif(days, n, profile, profile === "app" ? app : ""),
    enabled: ready,
  });
  const warn = advisory(summary);
  const rows = data?.lines ?? [];
  // The analysis behind the page was let go (its TTL, a refresh) and the new
  // one cannot answer yet: read the page again, whose views say what they need.
  const qc = useQueryClient();
  const stale = data !== undefined && !data.ready;
  useEffect(() => {
    if (stale) void qc.invalidateQueries({ queryKey: qk.capacity(days) });
  }, [stale, qc, days]);
  const option = useMemo<ChartOption>(
    () => ({
      animation: false,
      grid: { left: 8, right: 24, top: 8, bottom: 24, containLabel: true },
      tooltip: { trigger: "axis", valueFormatter: (v: unknown) => pct(Number(v) / 100) },
      xAxis: { type: "value", axisLabel: { formatter: "{value}%" } },
      yAxis: { type: "category", inverse: true, data: rows.filter((r) => r.capacity).map((r) => lineLabelById(t, summary.lines, r.id)) },
      series: [
        {
          type: "bar",
          data: rows
            .filter((r) => r.capacity)
            .map((r) => Math.round((((level === "p95" ? r.needed_p95 : r.needed_p50) ?? 0) / (r.capacity ?? 1)) * 1000) / 10),
          markLine: { symbol: "none", data: [{ xAxis: 100 }], label: { formatter: t("capacity.whatif.have") } },
          barMaxWidth: 14,
        },
      ],
    }),
    [rows, level, summary, t],
  );

  return (
    <section className="capacity-view" data-testid="capacity-whatif">
      <h3 className="capacity-h3">{t("capacity.whatif.title")}</h3>
      <NotReady readiness={summary.readiness.whatif} t={t} />
      {ready && (
        <>
          <form
            className="capacity-controls"
            onSubmit={(e) => {
              e.preventDefault();
              const v = Number.parseInt(draft, 10);
              if (Number.isFinite(v) && v >= 0) setN(v);
            }}
          >
            <label>
              {t("capacity.whatif.n")}
              <input
                className="input"
                type="number"
                min={0}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                autoComplete="off"
                data-testid="capacity-whatif-n"
                style={{ width: "7em" }}
              />
            </label>
            <Segmented
              label={t("capacity.whatif.profile")}
              value={profile}
              onChange={setProfile}
              testId="capacity-whatif-profile"
              options={(["all", "heavy", "app"] as const).map((v) => ({
                value: v,
                label: t(`capacity.whatif.profile.${v}`),
                disabled: v === "app" && apps.length === 0,
              }))}
            />
            {profile === "app" && (
              <label>
                {t("capacity.whatif.app")}
                <select className="input" value={app} onChange={(e) => setApp(e.target.value)}>
                  {apps.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <Segmented
              label={t("capacity.whatif.level")}
              value={level}
              onChange={setLevel}
              testId="capacity-whatif-level"
              options={(["p50", "p95"] as const).map((v) => ({ value: v, label: t(`capacity.whatif.level.${v}`) }))}
            />
            <button type="submit" className="btn" data-variant="primary" data-size="md" data-testid="capacity-whatif-run">
              {t("capacity.whatif.run")}
            </button>
          </form>
          <PeopleCaveat summary={summary} t={t} />
          {stale && (
            <p role="status" className="capacity-note" data-testid="capacity-whatif-stale">
              {t("capacity.whatif.stale")}
            </p>
          )}
          {warn !== null && (
            <p role="note" className="capacity-warning" data-testid="capacity-advisory">
              {t("capacity.whatif.advisory", { mape: pct(warn), ok: pct(summary.confidence.mape_ok) })}
            </p>
          )}
          {data && data.n !== data.n_asked && (
            <p role="note" className="capacity-warning" data-testid="capacity-whatif-capped">
              {t("capacity.whatif.capped", { n: data.n, asked: data.n_asked })}
            </p>
          )}
          {rows.length > 0 && (
            <>
              <Chart option={option} label={t("capacity.whatif.title")} height={Math.max(160, 28 * rows.length + 50)} testId="capacity-whatif-chart" />
              <div className="capacity-scroll">
                <table className="capacity-table" data-testid="capacity-whatif-table">
                  <thead>
                    <tr>
                      <th scope="col">{t("capacity.col.line")}</th>
                      <th scope="col">{t("capacity.whatif.needed")}</th>
                      <th scope="col">{t("capacity.whatif.have")}</th>
                      <th scope="col">{t("capacity.whatif.short")}</th>
                      <th scope="col">{t("capacity.whatif.machines")}</th>
                      <th scope="col">{t("capacity.whatif.fits")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const needed = level === "p95" ? r.needed_p95 : r.needed_p50;
                      const short = needed !== null && r.capacity !== null ? needed - r.capacity : null;
                      const machines = r.machines_needed[level];
                      const fits = r.fits[level];
                      return (
                        <tr key={r.id} data-fits={fits === null ? undefined : String(fits)}>
                          <th scope="row">{lineLabelById(t, summary.lines, r.id)}</th>
                          <td>
                            {formatValue(t, needed, r.unit)}
                            {r.fixed && <span className="capacity-sub">{t("capacity.whatif.fixed")}</span>}
                          </td>
                          <td>{r.capacity === null ? t("capacity.unknown") : formatValue(t, r.capacity, r.unit)}</td>
                          <td>{short !== null && short > 0 ? formatValue(t, short, r.unit) : "—"}</td>
                          <td>
                            {machines == null
                              ? "—"
                              : t(r.unit === "inflight" ? "capacity.whatif.cardsValue" : "capacity.whatif.machinesValue", {
                                  n: machines,
                                  now: r.machines_now == null ? "—" : machineCount(r.machines_now),
                                })}
                            {(r.unit === "inflight" || r.machine_label) && (
                              <span className="capacity-sub">
                                {r.unit === "inflight" ? t("capacity.machine.card") : r.machine_label}
                              </span>
                            )}
                          </td>
                          <td>{fits === null ? "—" : t(fits ? "capacity.whatif.yes" : "capacity.whatif.no")}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
      <Runway summary={summary} t={t} />
    </section>
  );
}

/** View 7 — when it runs short; order by. */
function Runway({ summary, t }: { summary: CapacitySummary; t: T }) {
  const [lead, setLead] = useState(String(DEFAULT_LEAD_DAYS));
  const leadDays = Math.max(0, Number.parseInt(lead, 10) || 0);
  const at = runwayDate(summary);
  // while N is still filling up, the runway counts no days: say why
  const recorded = recordedDays(summary);
  const option = useMemo<ChartOption | null>(() => {
    const { trend, days, n, band } = summary.runway;
    if (!trend || days.length === 0) return null;
    const labels = days.map((d) => dayLabel(summary.period.days[d]));
    const tenth = (v: number) => Math.round(v * 10) / 10;
    const fit = (d: number) => tenth(trend.intercept + trend.slope * d);
    const edge = (side: 0 | 1) => ({
      type: "line" as const,
      name: t("capacity.runway.band"),
      data: band.map((b) => tenth(b[side])),
      showSymbol: false,
      lineStyle: { type: "dotted" as const, opacity: 0.6 },
    });
    return {
      animation: false,
      grid: { left: 8, right: 16, top: 28, bottom: 24, containLabel: true },
      legend: { top: 0 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: labels },
      yAxis: { type: "value", minInterval: 1 },
      series: [
        { type: "line", name: t("capacity.trends.active30"), data: n, showSymbol: false },
        {
          type: "line",
          name: t("capacity.runway.trend"),
          data: days.map((d) => fit(d)),
          showSymbol: false,
          lineStyle: { type: "dashed" },
          markLine:
            summary.headroom?.max_n != null
              ? { symbol: "none", data: [{ yAxis: summary.headroom.max_n }], label: { formatter: "{c}" } }
              : undefined,
        },
        edge(1),
        edge(0),
      ],
    };
  }, [summary, t]);
  return (
    <section className="capacity-sub-view" data-testid="capacity-runway">
      <h3 className="capacity-h3">{t("capacity.runway.title")}</h3>
      {recorded !== null && (
        <p className="capacity-note" data-testid="capacity-runway-incomplete">
          {t("capacity.runway.peopleIncomplete", { days: recorded })}
        </p>
      )}
      <NotReady readiness={summary.readiness.runway} t={t} />
      {summary.readiness.runway.ready && (
        <>
          <label className="capacity-controls">
            {t("capacity.runway.lead")}
            <input
              className="input"
              type="number"
              min={0}
              value={lead}
              onChange={(e) => setLead(e.target.value)}
              autoComplete="off"
              data-testid="capacity-runway-lead"
              style={{ width: "6em" }}
            />
          </label>
          <p className="capacity-headline" data-testid="capacity-runway-text">
            {at.kind === "none" && t("capacity.runway.none")}
            {at.kind === "flat" && t("capacity.runway.flat")}
            {at.kind === "past" && t("capacity.runway.past")}
            {at.kind === "beyond" &&
              t("capacity.runway.beyond", {
                slope: Math.round(at.slope * 100) / 100,
                date: dateText(at.atMs),
                n: at.target,
              })}
            {at.kind === "date" && (
              <>
                {at.lateMs === null
                  ? t("capacity.runway.dateOpen", {
                      slope: Math.round(at.slope * 100) / 100,
                      date: dateText(at.atMs),
                      early: dateText(at.earlyMs),
                      n: at.target,
                    })
                  : t("capacity.runway.date", {
                      slope: Math.round(at.slope * 100) / 100,
                      date: dateText(at.atMs),
                      early: dateText(at.earlyMs),
                      late: dateText(at.lateMs),
                      n: at.target,
                    })}{" "}
                {orderText(t, at.earlyMs - leadDays * 86_400_000, summary.period.end_ms)}
              </>
            )}
          </p>
          {option && <Chart option={option} label={t("capacity.runway.title")} height={220} testId="capacity-runway-chart" />}
          <ul className="capacity-notes">
            {summary.runway.disk.map((d) => {
              const label = lineLabelById(t, summary.lines, d.line);
              const daysLeft = d.trend && d.now !== null ? (d.limit - d.now) / d.trend.slope : null;
              const whenMs = daysLeft === null ? null : summary.period.end_ms + Math.max(0, daysLeft) * 86_400_000;
              if (!d.trend || d.trend.slope <= 0 || whenMs === null || !(whenMs <= LAST_DATE_MS)) {
                return <li key={d.line}>{t("capacity.runway.diskFlat", { line: label })}</li>;
              }
              return (
                <li key={d.line}>
                  {t("capacity.runway.disk", {
                    line: label,
                    rate: bytes(d.trend.slope),
                    date: dateText(whenMs),
                    limit: bytes(d.limit),
                  })}
                  {d.per_person_month !== null &&
                    d.per_person_month > 0 &&
                    ` ${t("capacity.runway.diskPerPerson", { size: bytes(d.per_person_month) })}`}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
