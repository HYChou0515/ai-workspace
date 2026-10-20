/**
 * `/schedules` — every schedule the viewer may read, across items
 * (`docs/plan-schedule-overview.md`).
 *
 * The rows are the server's and so is the filter: `GET /schedules` returns the
 * schedules of every item the viewer may read, each with when it runs next,
 * how its last run went, and whether this viewer may change or run it. This
 * page arranges them — grouped or not, by next run or with what needs
 * attention first, as the viewer chooses (remembered per browser) — and
 * offers the four acts on a row: open it, run it now, move its time, remove it.
 *
 * Every time is on the VIEWER's clock with no zone label, and things are called
 * by the names people gave them — a workflow's title, a page's title
 * (`docs/plan-schedule-overview-polish.md`).
 *
 * "Last run" opens that execution's tab in its item, through the item page's
 * `?open=workflow-run:<id>` (plan-workflow-out-of-chat P11). It used to open the
 * schedule's own conversation (`?chat=`); an execution is not a chat any more,
 * and the chat list no longer shows that conversation.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { qk } from "../api/queryKeys";
import { type OverviewRow, type ScheduleOverview, type SchedulesApi, schedulesApi } from "../api/schedules";
import { AppTag } from "../components/AppTag";
import {
  ScheduleActions,
  ScheduleLast,
  ScheduleNext,
  ScheduleRunsAs,
  ScheduleWhere,
  workflowName,
} from "../components/schedules/ScheduleRowParts";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useApps } from "../hooks/useResources";
import { describeSchedule } from "../lib/describeSchedule";
import { useT } from "../lib/i18n";
import { type ScheduleSort, useScheduleOverviewPrefs } from "../lib/scheduleOverviewPrefs";
import { type ViewerClock, useViewerClock } from "../lib/viewerClock";

/** The last-run states that need somebody: it failed, it was stopped, or it is
 * waiting for a review — the "needs attention" sort puts these first. */
const ATTENTION = new Set(["error", "cancelled", "awaiting_human"]);

/** A run that has not finished yet — while one is on the page, it refreshes
 * often enough to see it finish. */
const IN_FLIGHT = new Set(["pending", "running"]);

/** How long until the page asks again: every few seconds while a run on it is
 * going (so "執行中" turns into how it went without a reload), every minute
 * otherwise (a schedule that fired, a time somebody else moved). */
export function refreshEvery(data: ScheduleOverview | undefined): number {
  const going = data?.rows.some((r) => IN_FLIGHT.has(r.last_run?.status ?? ""));
  return going ? 5000 : 60000;
}

const needsAttention = (r: OverviewRow) => ATTENTION.has(r.last_run?.status ?? "");

/** The rows in the chosen order. "next": what runs soonest first (rows in
 * different zones compared as one instant, `next_ms`), the rows that will not
 * run last in the server's order. "trouble": rows whose last run needs
 * attention first, most recent first, then the rest as "next". */
export function orderRows(rows: OverviewRow[], sort: ScheduleSort): OverviewRow[] {
  const byNext = (a: OverviewRow, b: OverviewRow) => {
    if (a.runnable !== b.runnable) return a.runnable ? -1 : 1;
    if (!a.runnable) return 0;
    return (a.next_ms ?? 0) - (b.next_ms ?? 0);
  };
  if (sort === "next") return [...rows].sort(byNext);
  const when = (r: OverviewRow) => r.last_run?.ended ?? r.last_run?.started ?? 0;
  return [...rows].sort((a, b) => {
    if (needsAttention(a) !== needsAttention(b)) return needsAttention(a) ? -1 : 1;
    if (needsAttention(a)) return when(b) - when(a);
    return byNext(a, b);
  });
}

export { placeOf } from "../components/schedules/ScheduleRowParts";

export function SchedulesOverviewPage({ client = schedulesApi }: { client?: SchedulesApi }) {
  const t = useT();
  useBreadcrumbs([{ label: t("nav.home"), to: "/" }, { label: t("scheduleOverview.title") }]);
  const [prefs, setPrefs] = useScheduleOverviewPrefs();
  const [appFilter, setAppFilter] = useState("");
  const apps = useApps();
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: qk.schedulesOverview,
    queryFn: () => client.overview(),
    refetchInterval: (query) => refreshEvery(query.state.data),
  });

  if (isError) {
    return (
      <div className="page">
        <h1>{t("scheduleOverview.title")}</h1>
        <p className="error" role="alert">
          {t("scheduleOverview.error")}{" "}
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            onClick={() => void refetch()}
          >
            {t("scheduleOverview.retry")}
          </button>
        </p>
      </div>
    );
  }
  if (isPending || !data) return <p>{t("scheduleOverview.loading")}</p>;

  const nothing = data.rows.length === 0 && data.files.length === 0;
  // The Apps present, first seen — the filter's options and the groups' order.
  const slugs = [...new Set([...data.rows, ...data.files].map((r) => r.slug))];
  // The filter follows the options (the WUI overview's rule): an App whose
  // last schedule went is no longer an option, and a select holding a value it
  // cannot show would draw 全部 while still filtering.
  const appPick = slugs.includes(appFilter) ? appFilter : "";
  const shown = orderRows(
    data.rows.filter((r) => !appPick || r.slug === appPick),
    prefs.sort,
  );
  const groups = new Map<string, OverviewRow[]>();
  for (const row of shown) {
    const key = prefs.group === "app" ? row.slug : "";
    const list = groups.get(key);
    if (list) list.push(row);
    else groups.set(key, [row]);
  }
  const brokenFiles = data.files.filter((f) => !appPick || f.slug === appPick);
  // "Needs attention first" with nothing that needs it changes nothing on
  // screen; say so, or the control looks dead.
  const calm = prefs.sort === "trouble" && shown.length > 0 && !shown.some(needsAttention);

  return (
    <div className="page page--wide">
      <h1>{t("scheduleOverview.title")}</h1>
      {!data.enabled ? (
        <p className="hint" role="status">
          {t("schedules.disabled")}
        </p>
      ) : null}
      {nothing ? (
        <>
          <p className="empty">{t("scheduleOverview.empty")}</p>
          <p className="hint">{t("scheduleOverview.empty.what")}</p>
        </>
      ) : (
        <>
          <div className="page-tools">
            <label>
              {t("scheduleOverview.filter.app")}
              <select
                className="input"
                value={appPick}
                onChange={(e) => setAppFilter(e.target.value)}
                aria-label={t("scheduleOverview.filter.app")}
              >
                <option value="">{t("scheduleOverview.filter.all")}</option>
                {slugs.map((slug) => (
                  <option key={slug} value={slug}>
                    {apps.find((a) => a.slug === slug)?.title || slug}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("scheduleOverview.group")}
              <select
                className="input"
                value={prefs.group}
                onChange={(e) => setPrefs({ group: e.target.value === "app" ? "app" : "none" })}
                aria-label={t("scheduleOverview.group")}
              >
                <option value="none">{t("scheduleOverview.group.none")}</option>
                <option value="app">{t("scheduleOverview.group.app")}</option>
              </select>
            </label>
            <label>
              {t("scheduleOverview.sort")}
              <select
                className="input"
                value={prefs.sort}
                onChange={(e) => setPrefs({ sort: e.target.value === "trouble" ? "trouble" : "next" })}
                aria-label={t("scheduleOverview.sort")}
              >
                <option value="next">{t("scheduleOverview.sort.next")}</option>
                <option value="trouble">{t("scheduleOverview.sort.trouble")}</option>
              </select>
            </label>
          </div>
          {calm ? (
            <p className="hint" role="status">
              {t("scheduleOverview.sort.calm")}
            </p>
          ) : null}
          {brokenFiles.map((f) => (
            <p key={`${f.item_id}${f.path}`} className="detail schedule-file-problem" role="alert">
              <Link to={`/a/${encodeURIComponent(f.slug)}/${encodeURIComponent(f.item_id)}`}>
                {f.item_title || f.item_id}
              </Link>{" "}
              {f.path} — {t("schedules.fileProblems")} {f.problems.join(" ")}
            </p>
          ))}
          {shown.length === 0 && brokenFiles.length === 0 ? (
            <p className="empty">{t("scheduleOverview.nomatch")}</p>
          ) : null}
          {shown.length > 0 ? <ScheduleTable groups={groups} client={client} canAssign={data.can_assign} /> : null}
        </>
      )}
    </div>
  );
}

/** ONE table, grouped or not: a group is a heading row inside it, so the
 * columns line up from the first group to the last (a table per group sized
 * each one's columns to its own content). */
function ScheduleTable({
  groups,
  client,
  canAssign,
}: {
  groups: Map<string, OverviewRow[]>;
  client: SchedulesApi;
  canAssign: boolean;
}) {
  const t = useT();
  const clock = useViewerClock();
  return (
    <div className="schedule-scroll">
      <table className="schedule-table">
        <thead>
          <tr>
            <th scope="col">{t("scheduleOverview.col.where")}</th>
            <th scope="col">{t("scheduleOverview.col.workflow")}</th>
            <th scope="col">{t("scheduleOverview.col.when")}</th>
            <th scope="col">{t("scheduleOverview.col.next")}</th>
            <th scope="col">{t("scheduleOverview.col.last")}</th>
            <th scope="col">{t("scheduleOverview.col.runsAs")}</th>
            <th scope="col">{t("scheduleOverview.col.actions")}</th>
          </tr>
        </thead>
        {[...groups].map(([slug, rows]) => (
          <tbody key={slug || "all"}>
            {slug ? (
              <tr className="schedule-group">
                <th scope="colgroup" colSpan={7}>
                  <AppTag slug={slug} />
                </th>
              </tr>
            ) : null}
            {rows.map((row) => (
              <ScheduleRowView
                key={`${row.item_id}${row.path}#${row.index}`}
                row={row}
                client={client}
                clock={clock}
                canAssign={canAssign}
              />
            ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}

/** One row: the shared parts (`components/schedules/ScheduleRowParts`), laid
 * out as table cells. */
function ScheduleRowView({
  row,
  client,
  clock,
  canAssign,
}: {
  row: OverviewRow;
  client: SchedulesApi;
  clock: ViewerClock;
  canAssign: boolean;
}) {
  const t = useT();
  const period = describeSchedule(row, clock, t);
  return (
    <tr data-testid={`schedule-${row.item_id}${row.path}#${row.index}`}>
      <td>
        <ScheduleWhere row={row} withItem />
      </td>
      <td title={row.run_title && row.run !== row.run_title ? row.run : undefined}>{workflowName(row)}</td>
      <td className="schedule-when" title={period.set || undefined}>
        {period.text}
      </td>
      <td className={row.runnable ? "schedule-next" : "schedule-next schedule-problem"}>
        <ScheduleNext row={row} clock={clock} />
      </td>
      <td className="schedule-last">
        <ScheduleLast row={row} clock={clock} />
      </td>
      <td className="schedule-runs-as">
        <ScheduleRunsAs row={row} canAssign={canAssign} />
      </td>
      <td>
        <ScheduleActions row={row} client={client} clock={clock} />
      </td>
    </tr>
  );
}
