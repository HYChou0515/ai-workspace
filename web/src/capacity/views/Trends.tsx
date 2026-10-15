/**
 * View 5 — 趨勢 (docs/plan-capacity-usage.md §10): is use growing, how fast —
 * each line's daily peak and average against its capacity and safe threshold,
 * and how many people there are.
 */
import { useMemo, useState } from "react";

import type { CapacitySummary } from "../api";
import { Chart } from "../Chart";
import type { ChartOption } from "../echarts";
import { dayLabel, formatValue, lineLabel, type T } from "../format";
import { NotReady } from "../Readiness";

export function Trends({ summary, t }: { summary: CapacitySummary; t: T }) {
  const lines = summary.lines.filter((l) => l.daily.peak.some((v) => v !== null));
  const [lineId, setLineId] = useState(lines[0]?.id ?? "");
  const line = lines.find((l) => l.id === lineId) ?? lines[0];
  const days = summary.period.days.map(dayLabel);

  const lineOption = useMemo<ChartOption | null>(() => {
    if (!line) return null;
    const marks: { yAxis: number; name: string }[] = [];
    if (line.capacity !== null) marks.push({ yAxis: line.capacity, name: t("capacity.trends.capacity") });
    if (line.capacity !== null && line.threshold !== null && line.unit !== "inflight") {
      marks.push({ yAxis: line.capacity * line.threshold, name: t("capacity.trends.threshold") });
    }
    return {
      animation: false,
      grid: { left: 8, right: 16, top: 28, bottom: 24, containLabel: true },
      legend: { top: 0 },
      tooltip: { trigger: "axis", valueFormatter: (v: unknown) => formatValue(t, Number(v), line.unit) },
      xAxis: { type: "category", data: days },
      yAxis: { type: "value", axisLabel: { formatter: (v: number) => formatValue(t, v, line.unit) } },
      series: [
        {
          type: "line",
          name: t("capacity.trends.peak"),
          data: line.daily.peak,
          showSymbol: false,
          markLine: { symbol: "none", data: marks, label: { formatter: "{b}" } },
        },
        { type: "line", name: t("capacity.trends.avg"), data: line.daily.avg, showSymbol: false },
      ],
    };
  }, [line, days, t]);

  const peopleOption = useMemo<ChartOption>(
    () => ({
      animation: false,
      grid: { left: 8, right: 16, top: 28, bottom: 24, containLabel: true },
      legend: { top: 0 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: days },
      yAxis: { type: "value", minInterval: 1 },
      series: [
        { type: "line", name: t("capacity.trends.active30"), data: summary.people.active_30d, showSymbol: false },
        { type: "bar", name: t("capacity.trends.today"), data: summary.people.active_today, barMaxWidth: 10 },
      ],
    }),
    [summary, days, t],
  );

  return (
    <section className="capacity-view" data-testid="capacity-trends">
      <NotReady readiness={summary.readiness.trends} t={t} />
      {line && lineOption && (
        <>
          <h3 className="capacity-h3">{t("capacity.trends.daily")}</h3>
          <div className="capacity-controls">
            <select
              className="input"
              value={line.id}
              onChange={(e) => setLineId(e.target.value)}
              aria-label={t("capacity.alloc.resource")}
              data-testid="capacity-trends-line"
            >
              {lines.map((l) => (
                <option key={l.id} value={l.id}>
                  {lineLabel(t, l)}
                </option>
              ))}
            </select>
          </div>
          <Chart option={lineOption} label={lineLabel(t, line)} testId="capacity-trends-chart" />
        </>
      )}
      <h3 className="capacity-h3">{t("capacity.trends.people")}</h3>
      <Chart option={peopleOption} label={t("capacity.trends.people")} height={200} testId="capacity-people-chart" />
    </section>
  );
}
