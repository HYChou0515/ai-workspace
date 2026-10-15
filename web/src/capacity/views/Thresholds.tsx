/**
 * Views 8–9 — 門檻與可信度 (docs/plan-capacity-usage.md §10): at what load it
 * gets slow (the felt-most slowness first, D13), and whether these numbers can
 * be trusted — backtest, fit, coverage, attribution gaps, assumptions.
 */
import { useMemo } from "react";

import type { CapacitySummary, Pair } from "../api";
import { Chart } from "../Chart";
import type { ChartOption } from "../echarts";
import { isMsgKey } from "../../lib/i18n";
import { lineLabelById, pct, purposeLabel, type T } from "../format";
import { NotReady, sourceName } from "../Readiness";

/** D13: (a), (b), (d), (e) are felt most and come first; (c) after. */
const ORDER: Pair["id"][] = ["a", "b", "d", "e", "gpu", "c"];
const FELT = new Set<Pair["id"]>(["a", "b", "d", "e"]);

function isShare(x: string): boolean {
  return x.endsWith(".util");
}

export function kneeText(t: T, summary: CapacitySummary, pair: Pair): string {
  if (pair.note === "no_psi") return t("capacity.pair.noPsi");
  const fmt = (v: number) => (isShare(pair.x) ? pct(v) : String(Math.round(v * 100) / 100));
  if (pair.knee) {
    if (pair.sets) {
      const line = summary.lines.find((l) => l.id === pair.sets);
      // a model's knee is its capacity (E5), any other line's its safe threshold
      const key = line?.unit === "inflight" ? "capacity.knee.atCapacity" : "capacity.knee.at";
      return t(key, { at: fmt(pair.knee.at), line: lineLabelById(t, summary.lines, pair.sets) });
    }
    return t("capacity.knee.atShown", { at: fmt(pair.knee.at) });
  }
  if (isShare(pair.x) && pair.span < 0.2) return t("capacity.knee.narrow", { span: pct(pair.span) });
  return t("capacity.knee.none", { max: pair.x_max === null ? "—" : fmt(pair.x_max) });
}

/** A pair's name: (b) also per model, (e) per job type. */
export function pairTitle(t: T, pair: Pair): string {
  if (pair.id === "b" && pair.model) return t("capacity.pair.bModel", { model: pair.model });
  if (pair.id === "e" && pair.job) return t("capacity.pair.eJob", { job: purposeLabel(t, pair.job) });
  return t(`capacity.pair.${pair.id}`, { model: pair.model });
}

/** One card per pair: the same id comes once per model and per job type. */
export function pairKey(pair: Pair): string {
  return [pair.id, pair.model, pair.job].filter(Boolean).join("-");
}

function scatter(t: T, pair: Pair, title: string): ChartOption {
  const share = isShare(pair.x);
  const x = (v: number) => (share ? Math.round(v * 1000) / 10 : v);
  const kneeLine = pair.knee
    ? { symbol: "none", data: [{ xAxis: x(pair.knee.at) }], label: { formatter: "{c}" } }
    : undefined;
  const held = pair.throttled ?? [];
  return {
    animation: false,
    grid: { left: 8, right: 24, top: held.length ? 28 : 8, bottom: 24, containLabel: true },
    legend: held.length ? { top: 0 } : undefined,
    tooltip: { trigger: "item" },
    xAxis: { type: "value", axisLabel: share ? { formatter: "{value}%" } : {} },
    yAxis: { type: "value", name: title.length > 40 ? undefined : title },
    series: [
      {
        type: "scatter",
        name: pair.id === "d" ? t("capacity.pair.d.waited") : title,
        symbolSize: 4,
        data: pair.points.map(([px, py]) => [x(px), py]),
        markLine: kneeLine,
      },
      ...(held.length
        ? [
            {
              type: "scatter" as const,
              name: t("capacity.pair.d.held"),
              symbol: "diamond",
              symbolSize: 4,
              data: held.map(([px, py]) => [x(px), py]),
            },
          ]
        : []),
    ],
  };
}

export function Thresholds({ summary, t }: { summary: CapacitySummary; t: T }) {
  const pairs = [...summary.thresholds].sort((a, b) => ORDER.indexOf(a.id) - ORDER.indexOf(b.id));
  return (
    <section className="capacity-view" data-testid="capacity-thresholds">
      <NotReady readiness={summary.readiness.thresholds} t={t} />
      {summary.readiness.thresholds.ready &&
        pairs.map((pair) => <PairCard key={pairKey(pair)} pair={pair} summary={summary} t={t} />)}
      <Confidence summary={summary} t={t} />
    </section>
  );
}

function PairCard({ pair, summary, t }: { pair: Pair; summary: CapacitySummary; t: T }) {
  const title = pairTitle(t, pair);
  const option = useMemo(() => scatter(t, pair, title), [t, pair, title]);
  return (
    <article className="capacity-card" data-testid={`capacity-pair-${pairKey(pair)}`}>
      <header>
        <h3 className="capacity-h3">{title}</h3>
        {FELT.has(pair.id) && <span className="capacity-badge">{t("capacity.pair.felt")}</span>}
      </header>
      <p className="capacity-note" data-testid={`capacity-knee-${pair.id}`}>
        {kneeText(t, summary, pair)}
      </p>
      {(pair.points.length > 0 || (pair.throttled?.length ?? 0) > 0) && (
        <Chart option={option} label={title} height={220} />
      )}
    </article>
  );
}

/** A regression column in words: "req:GET /api/a/{slug}/items" → a request. */
export function columnLabel(t: T, column: string): string {
  const other = /^req:\(other ([A-Z]+)\)$/.exec(column);
  if (other) return t("capacity.column.otherRequests", { method: other[1] });
  if (column.startsWith("req:")) {
    const [method, path = ""] = column.slice(4).split(" ", 2);
    return t("capacity.column.request", { method, path: path.split("/").filter(Boolean).slice(-2).join("/") });
  }
  if (column === "stream") return purposeLabel(t, "streams");
  const [kind, name = ""] = column.split(":", 2);
  if (kind === "turn-events") return t("capacity.column.turnEvents", { surface: purposeLabel(t, name) });
  if (kind === "turn") return t("capacity.column.turns", { surface: purposeLabel(t, name) });
  if (kind === "job") return t("capacity.column.jobs", { job: purposeLabel(t, name) });
  return column;
}

/** What a source's coverage counts: slots with a sample, or — for the records
 * and LiteLLM, which have rows only when something happened — with activity. */
function coverageText(t: T, source: string, share: number | null): string {
  const key = `capacity.conf.coverage.${source}`;
  return isMsgKey(key)
    ? t(key, { pct: pct(share) })
    : t("capacity.conf.coverage", { source: sourceName(t, source), pct: pct(share) });
}

function when(ms: number): string {
  return new Date(ms).toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** View 9 — can these numbers be trusted. */
function Confidence({ summary, t }: { summary: CapacitySummary; t: T }) {
  const c = summary.confidence;
  return (
    <section className="capacity-sub-view" data-testid="capacity-confidence">
      <h3 className="capacity-h3">{t("capacity.conf.title")}</h3>
      <NotReady readiness={summary.readiness.confidence} t={t} />
      <ul className="capacity-notes">
        {c.backtest ? (
          <li data-testid="capacity-backtest">
            {t("capacity.conf.backtest", { days: c.backtest.days.length, mape: pct(c.backtest.mape, 1) })}{" "}
            {c.backtest.mape <= c.mape_ok
              ? t("capacity.conf.backtestOk", { ok: pct(c.mape_ok) })
              : t("capacity.conf.backtestBad", { ok: pct(c.mape_ok) })}
          </li>
        ) : (
          summary.readiness.confidence.ready && <li>{t("capacity.conf.noBacktest")}</li>
        )}
        {c.regressions.map((r) => (
          <li key={r.line}>
            {t("capacity.conf.fit", {
              line: lineLabelById(t, summary.lines, r.line),
              r2: pct(r.r2),
              days: r.holdout_days,
              mape: pct(r.holdout_mape, 1),
            })}
            {r.collinear.map(([a, b]) => (
              <span key={`${a}|${b}`} className="capacity-sub">
                {t("capacity.conf.collinear", { a: columnLabel(t, a), b: columnLabel(t, b) })}
              </span>
            ))}
          </li>
        ))}
        {Object.entries(c.coverage)
          .filter(([, v]) => v !== null)
          .map(([source, v]) => (
            <li key={source}>{coverageText(t, source, v)}</li>
          ))}
        {Object.entries(c.gaps).flatMap(([source, gaps]) =>
          gaps.map(([start, end]) => (
            <li key={`${source}-${start}`} data-testid="capacity-gap">
              {t("capacity.conf.gap", { source: sourceName(t, source), start: when(start), end: when(end) })}
            </li>
          )),
        )}
        {c.unknown_share !== null && c.unknown_share > 0 && (
          <li>{t("capacity.conf.unknown", { pct: pct(c.unknown_share, 1) })}</li>
        )}
        {c.system_share !== null && c.system_share > 0 && (
          <li>{t("capacity.conf.system", { pct: pct(c.system_share, 1) })}</li>
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
