/**
 * One chart of the capacity dashboard. ECharts arrives on first draw through
 * `import("./echarts")` — its own chunk — so no other page pays for it (D15).
 * The `aria-label` says what a chart shows. The answers a decision rests on are
 * also in words or a table beside it — the overview, allocation and what-if
 * tables, the knee sentences, the runway's dates — so a screen reader loses none.
 */
import { useEffect, useRef, useState } from "react";

import type { ChartOption, ECharts } from "./echarts";

export function Chart({
  option,
  label,
  height = 260,
  testId,
}: {
  option: ChartOption;
  label: string;
  height?: number;
  testId?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<ECharts | null>(null);
  const latest = useRef(option);
  const [failed, setFailed] = useState(false);
  latest.current = option;

  useEffect(() => {
    let alive = true;
    let observer: ResizeObserver | undefined;
    import("./echarts")
      .then(({ echarts }) => {
        if (!alive || !host.current) return;
        const c = echarts.init(host.current, undefined, { renderer: "canvas" });
        chart.current = c;
        c.setOption(latest.current, true);
        if (typeof ResizeObserver !== "undefined") {
          observer = new ResizeObserver(() => c.resize());
          observer.observe(host.current);
        }
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
      observer?.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, true);
  }, [option]);

  return (
    <div
      ref={host}
      role="img"
      aria-label={label}
      data-testid={testId}
      data-failed={failed || undefined}
      className="capacity-chart"
      style={{ height }}
    />
  );
}
