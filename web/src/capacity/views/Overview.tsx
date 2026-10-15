/**
 * View 1 — 總覽 (docs/plan-capacity-usage.md §10): how much is used, how far
 * from the limit, which resource runs out first, and in one sentence how many
 * people today's habits would hold.
 */
import { useMemo } from "react";

import type { CapacitySummary, Line } from "../api";
import { Chart } from "../Chart";
import type { ChartOption } from "../echarts";
import { formatValue, lineLabel, lineLabelById, pct, type T } from "../format";
import { NotReady, readinessText, sourceName } from "../Readiness";

export function headline(t: T, s: CapacitySummary): string {
  const room = s.headroom;
  if (!s.readiness.whatif.ready) return readinessText(t, s.readiness.whatif);
  if (room === null || room.max_n === null) return t("capacity.headline.noLimit");
  const line = lineLabelById(t, s.lines, room.binding);
  if (room.ceiling) return t("capacity.headline.ceiling", { n: room.max_n, now: room.n_now });
  if (room.max_n < room.n_now) {
    return t("capacity.headline.over", { n: room.max_n, now: room.n_now, line });
  }
  return t("capacity.headline.room", { n: room.max_n, now: room.n_now, line });
}

/** The lines worth a row: anything measured with a peak. */
export function overviewLines(s: CapacitySummary): Line[] {
  return s.lines.filter((l) => l.peak !== null);
}

export function Overview({ summary, t }: { summary: CapacitySummary; t: T }) {
  const lines = overviewLines(summary);
  const room = summary.headroom;
  const option = useMemo<ChartOption>(() => utilisationChart(t, lines), [t, lines]);
  return (
    <section className="capacity-view" data-testid="capacity-overview">
      <p className="capacity-headline" data-testid="capacity-headline">
        {headline(t, summary)}
      </p>
      {summary.readiness.whatif.ready && room?.max_n !== null && (
        <p className="capacity-note">{t("capacity.headline.basis", { days: summary.period.days.length })}</p>
      )}
      <NotReady readiness={summary.readiness.overview} t={t} />
      {lines.length > 0 && (
        <>
          <Chart option={option} label={t("capacity.col.utilization")} height={Math.max(160, 28 * lines.length + 60)} testId="capacity-overview-chart" />
          <div className="capacity-scroll">
            <table className="capacity-table">
              <thead>
                <tr>
                  <th scope="col">{t("capacity.col.line")}</th>
                  <th scope="col">{t("capacity.col.peak")}</th>
                  <th scope="col">{t("capacity.col.capacity")}</th>
                  <th scope="col">{t("capacity.col.threshold")}</th>
                  <th scope="col">{t("capacity.col.utilization")}</th>
                  <th scope="col">{t("capacity.col.people")}</th>
                </tr>
              </thead>
              <tbody>
                {lines.map((line) => {
                  const binding = room?.binding === line.id;
                  const holds = room?.by_line[line.id];
                  return (
                    <tr key={line.id} data-binding={binding || undefined} data-testid={`capacity-row-${line.id}`}>
                      <th scope="row">
                        {lineLabel(t, line)}
                        {binding && <span className="capacity-badge">{t("capacity.binding")}</span>}
                      </th>
                      <td>{formatValue(t, line.peak, line.unit)}</td>
                      <td>
                        {line.capacity === null ? t("capacity.unknown") : formatValue(t, line.capacity, line.unit)}
                        {line.machine_label && <span className="capacity-sub">{line.machine_label}</span>}
                      </td>
                      <td>
                        {line.threshold === null ? "—" : line.unit === "inflight" ? "—" : pct(line.threshold)}
                        {line.threshold_source && (
                          <span className="capacity-sub">
                            {t(line.threshold_source === "knee" ? "capacity.threshold.knee" : "capacity.threshold.default")}
                          </span>
                        )}
                      </td>
                      <td>{pct(line.utilization_peak)}</td>
                      <td>{holds === undefined ? "—" : holds}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      <h3 className="capacity-h3">{t("capacity.sources.title")}</h3>
      <ul className="capacity-sources" data-testid="capacity-sources">
        {Object.entries(summary.sources).map(([name, s]) => (
          <li key={name} data-state={s.error ? "error" : s.connected ? "ok" : "off"}>
            <span>{sourceName(t, name)}</span>
            <span className="capacity-sub">
              {s.error
                ? t("capacity.sources.error", { error: s.error })
                : s.connected
                  ? t("capacity.sources.connected", { rows: s.rows.toLocaleString() })
                  : t("capacity.sources.notConnected")}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function utilisationChart(t: T, lines: Line[]): ChartOption {
  const shown = lines.filter((l) => l.utilization_peak !== null);
  const names = shown.map((l) => lineLabel(t, l));
  return {
    animation: false,
    grid: { left: 8, right: 24, top: 8, bottom: 24, containLabel: true },
    tooltip: { trigger: "axis", valueFormatter: (v: unknown) => pct(Number(v) / 100) },
    xAxis: { type: "value", axisLabel: { formatter: "{value}%" }, max: (v: { max: number }) => Math.max(100, Math.ceil(v.max / 10) * 10) },
    yAxis: { type: "category", data: names, inverse: true },
    series: [
      {
        type: "bar",
        name: t("capacity.col.utilization"),
        data: shown.map((l) => Math.round((l.utilization_peak ?? 0) * 1000) / 10),
        barMaxWidth: 16,
      },
      {
        type: "scatter",
        name: t("capacity.col.threshold"),
        symbol: "rect",
        symbolSize: [3, 18],
        data: shown.map((l) => (l.threshold !== null && l.unit !== "inflight" ? l.threshold * 100 : null)),
      },
    ],
  };
}
