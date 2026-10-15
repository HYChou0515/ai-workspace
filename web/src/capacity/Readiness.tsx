/**
 * What a view still needs before it says anything (docs/plan-capacity-usage.md
 * §9.10, D14): a source not connected or failing, a period shorter than the
 * view needs (no wait will fix that — a longer period will), days still
 * accumulating, too few people. Only "accumulating" appears by itself.
 */
import { CAPACITY_DAYS, type Readiness } from "./api";
import { list, type T } from "./format";
import { isMsgKey } from "../lib/i18n";

export function sourceName(t: T, source: string): string {
  const key = `capacity.source.${source}`;
  return isMsgKey(key) ? t(key) : source;
}

/** The shortest period the page offers that holds `need` whole days. */
export function longerPeriod(need: number): number {
  return CAPACITY_DAYS.find((days) => days >= need) ?? CAPACITY_DAYS[CAPACITY_DAYS.length - 1];
}

export function readinessText(t: T, r: Readiness): string {
  const sources = list(
    t,
    r.missing.map((s) => sourceName(t, s)),
  );
  switch (r.reason) {
    case "source_error":
      return t("capacity.ready.source_error", { sources, error: r.error ?? "" });
    case "source_missing":
      return t("capacity.ready.source_missing", { sources });
    case "period_too_short":
      return t("capacity.ready.period_too_short", {
        need: r.need_days,
        have: r.period_days,
        suggest: longerPeriod(r.need_days),
      });
    case "accumulating":
      return t("capacity.ready.accumulating", { have: r.have_days, need: r.need_days });
    case "too_few_people":
      return t("capacity.ready.too_few_people");
    case "no_regression":
      return t("capacity.ready.no_regression");
    default:
      return "";
  }
}

/** Nothing when ready; otherwise the one sentence saying what is missing. */
export function NotReady({ readiness, t }: { readiness: Readiness; t: T }) {
  if (readiness.ready) return null;
  return (
    <div role="status" className="capacity-notready" data-testid="capacity-notready">
      {readinessText(t, readiness)}
    </div>
  );
}
