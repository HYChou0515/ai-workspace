/**
 * Views 8–9 — 門檻與可信度 (docs/plan-capacity-usage.md §10): at what load it
 * gets slow (the felt-most slowness first, D13), and whether these numbers can
 * be trusted — backtest, fit, coverage, attribution gaps, assumptions.
 */
import { useMemo } from "react";

import type { CapacitySummary, Pair } from "../api";
import { Chart } from "../Chart";
import type { ChartOption } from "../echarts";
import { lineLabelById, pct, type T } from "../format";
import { NotReady, sourceName } from "../Readiness";

/** D13: (a), (b), (d), (e) are felt most and come first; (c) after. */
const ORDER: Pair["id"][] = ["a", "b", "d", "e", "gpu", "c"];
const FELT = new Set<Pair["id"]>(["a", "b", "d", "e"]);

function isShare(x: string): boolean {
  return x.endsWith(".util");
}

export function kneeText(t: T, summary: CapacitySummary, pair: Pair): string {
  const fmt = (v: number) => (isShare(pair.x) ? pct(v) : String(Math.round(v * 100) / 100));
  if (pair.knee) {
    if (pair.sets) {
      return t("capacity.knee.at", { at: fmt(pair.knee.at), line: lineLabelById(t, summary.lines, pair.sets) });
    }
    return t("capacity.knee.atShown", { at: fmt(pair.knee.at) });
  }
  if (isShare(pair.x) && pair.span < 0.2) return t("capacity.knee.narrow", { span: pct(pair.span) });
  return t("capacity.knee.none", { max: pair.x_max === null ? "—" : fmt(pair.x_max) });
}

function pairTitle(t: T, pair: Pair): string {
  return t(`capacity.pair.${pair.id}`, { model: pair.model });
}

function scatter(pair: Pair, title: string): ChartOption {
  const share = isShare(pair.x);
  const kneeLine = pair.knee
    ? { symbol: "none", data: [{ xAxis: share ? pair.knee.at * 100 : pair.knee.at }], label: { formatter: "{c}" } }
    : undefined;
  const yCap = pair.y.endsWith("per_token") ? (v: number) => (v > 0 ? 1 / v : 0) : (v: number) => v;
  return {
    animation: false,
    grid: { left: 8, right: 24, top: 8, bottom: 24, containLabel: true },
    tooltip: { trigger: "item" },
    xAxis: { type: "value", axisLabel: share ? { formatter: "{value}%" } : {} },
    yAxis: { type: "value", name: title.length > 40 ? undefined : title },
    series: [
      {
        type: "scatter",
        symbolSize: 4,
        data: pair.points.map(([x, y]) => [share ? Math.round(x * 1000) / 10 : x, yCap(y)]),
        markLine: kneeLine,
      },
    ],
  };
}

export function Thresholds({ summary, t }: { summary: CapacitySummary; t: T }) {
  const pairs = [...summary.thresholds].sort((a, b) => ORDER.indexOf(a.id) - ORDER.indexOf(b.id));
  return (
    <section className="capacity-view" data-testid="capacity-thresholds">
      <NotReady readiness={summary.readiness.thresholds} t={t} />
      {summary.readiness.thresholds.ready &&
        pairs.map((pair) => <PairCard key={`${pair.id}-${pair.model}`} pair={pair} summary={summary} t={t} />)}
      <Confidence summary={summary} t={t} />
    </section>
  );
}

function PairCard({ pair, summary, t }: { pair: Pair; summary: CapacitySummary; t: T }) {
  const title = pairTitle(t, pair);
  const option = useMemo(() => scatter(pair, title), [pair, title]);
  return (
    <article className="capacity-card" data-testid={`capacity-pair-${pair.id}`}>
      <header>
        <h3 className="capacity-h3">{title}</h3>
        {FELT.has(pair.id) && <span className="capacity-badge">{t("capacity.pair.felt")}</span>}
      </header>
      <p className="capacity-note" data-testid={`capacity-knee-${pair.id}`}>
        {kneeText(t, summary, pair)}
      </p>
      {pair.points.length > 0 && <Chart option={option} label={title} height={220} />}
    </article>
  );
}

/** View 9 — can these numbers be trusted. */
function Confidence({ summary, t }: { summary: CapacitySummary; t: T }) {
  const c = summary.confidence;
  return (
    <section className="capacity-sub-view" data-testid="capacity-confidence">
      <h3 className="capacity-h3">{t("capacity.conf.title")}</h3>
      <ul className="capacity-notes">
        {c.backtest ? (
          <li data-testid="capacity-backtest">
            {t("capacity.conf.backtest", { days: c.backtest.days.length, mape: pct(c.backtest.mape, 1) })}{" "}
            {c.backtest.mape <= c.mape_ok
              ? t("capacity.conf.backtestOk", { ok: pct(c.mape_ok) })
              : t("capacity.conf.backtestBad", { ok: pct(c.mape_ok) })}
          </li>
        ) : (
          <li>{t("capacity.conf.noBacktest")}</li>
        )}
        {c.regressions.map((r) => (
          <li key={r.line}>
            {t("capacity.conf.fit", {
              line: lineLabelById(t, summary.lines, r.line),
              r2: pct(r.r2),
              mape: pct(r.holdout_mape, 1),
            })}
            {r.collinear.map(([a, b]) => (
              <span key={`${a}|${b}`} className="capacity-sub">
                {t("capacity.conf.collinear", { a, b })}
              </span>
            ))}
          </li>
        ))}
        {Object.entries(c.coverage)
          .filter(([, v]) => v !== null)
          .map(([source, v]) => (
            <li key={source}>{t("capacity.conf.coverage", { source: sourceName(t, source), pct: pct(v) })}</li>
          ))}
        {c.unknown_share !== null && c.unknown_share > 0 && (
          <li>{t("capacity.conf.unknown", { pct: pct(c.unknown_share, 1) })}</li>
        )}
        {Object.entries(c.untagged_gpu_share).map(([model, share]) => (
          <li key={model}>{t("capacity.conf.untagged", { model, pct: pct(share) })}</li>
        ))}
        {c.dropped > 0 && <li>{t("capacity.conf.dropped", { n: c.dropped.toLocaleString() })}</li>}
      </ul>
      <h4 className="capacity-h4">{t("capacity.conf.assumptions")}</h4>
      <ul className="capacity-notes">
        <li>{t("capacity.conf.assume.regression")}</li>
        <li>{t("capacity.conf.assume.fixed")}</li>
        <li>{t("capacity.conf.assume.bootstrap")}</li>
        <li>{t("capacity.conf.assume.gpu")}</li>
      </ul>
    </section>
  );
}
