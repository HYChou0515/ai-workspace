/**
 * What a view still needs before it says anything (docs/plan-capacity-usage.md
 * §9.10, D14): a source not connected, days still accumulating, too few people.
 * It appears by itself once the data is there.
 */
import type { Readiness } from "./api";
import type { T } from "./format";
import { isMsgKey } from "../lib/i18n";

export function sourceName(t: T, source: string): string {
  const key = `capacity.source.${source}`;
  return isMsgKey(key) ? t(key) : source;
}

export function readinessText(t: T, r: Readiness): string {
  switch (r.reason) {
    case "source_missing":
      return t("capacity.ready.source_missing", {
        sources: r.missing.map((s) => sourceName(t, s)).join("、"),
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
