/**
 * One dimension of a budget: what it is, how much of it is held, and how close
 * that is to the ceiling. Drawn on `/my-resources` (three totals, one storage
 * gauge) and in an item's Sandbox modal (the owner's CPU and memory totals).
 *
 * It lived as a page-private function on `/my-resources` while the modal kept
 * its own copy of the bar — the second copy is how the modal's total came to
 * show CPU alone, unnamed. One component; the CSS is `styles/gauge.css`.
 *
 * An UNLIMITED dimension still shows its usage — with no denominator and no
 * bar, because there is nothing to be a fraction of. Hidden entirely, a deploy
 * that caps only the sandbox count left people unable to see how much CPU or
 * memory they were holding at all.
 */

import { useT } from "../lib/i18n";

export function formatAgainstLimit(
  used: number,
  limit: number,
  render: (n: number) => string,
): string {
  return limit ? `${render(used)} / ${render(limit)}` : render(used);
}

export function Meter({ used, limit }: { used: number; limit: number }) {
  if (!limit) return null;
  // Capped: a quota lowered under a running sandbox puts `used` above `limit`,
  // and the fill must not leave its track.
  const pct = Math.min(100, Math.round((used / limit) * 100));
  return (
    <div className="meter" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className="meter-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Gauge({
  label,
  used,
  limit,
  format,
}: {
  label: string;
  used: number;
  limit: number;
  format: (n: number) => string;
}) {
  const t = useT();
  return (
    <div className="gauge">
      <p className="summary">
        <span className="gauge-label">{label}</span>
        <span className="gauge-value">{formatAgainstLimit(used, limit, format)}</span>
        {limit ? null : <span className="detail">{t("resources.gauge.unlimited")}</span>}
      </p>
      <Meter used={used} limit={limit} />
    </div>
  );
}
