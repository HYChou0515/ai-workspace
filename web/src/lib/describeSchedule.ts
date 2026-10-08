/**
 * A schedule row's recurrence in the reader's words — shared by the item's
 * Workflows panel and the schedules overview (`docs/plan-schedule-overview.md`),
 * so the two pages cannot describe one row two ways.
 *
 * On the VIEWER's clock (`docs/plan-schedule-overview-polish.md`): `text` is the
 * period moved into their zone with no zone label, `set` is what the row was
 * written as, for the hover. Only vocabulary: when a row fires NEXT is computed
 * on the backend, by the same rule the sweep fires it by, and arrives as
 * `next_ms`, which also picks the daylight saving the period is moved with.
 */
import type { useT } from "./i18n";
import { periodText } from "./scheduleTime";
import type { ViewerClock } from "./viewerClock";

export function describeSchedule(
  row: { raw: unknown; next_ms?: number | null },
  clock: ViewerClock,
  t: ReturnType<typeof useT>,
): { text: string; set: string } {
  return periodText(row.raw, { ...clock, nextMs: row.next_ms }, t);
}
