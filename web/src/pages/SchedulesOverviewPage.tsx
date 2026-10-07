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
 * "Last run" opens the schedule's own conversation — every run of one schedule
 * writes there — through the item's `?chat=` deep link; `from=schedules` tells
 * that page the reader came from here.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { qk } from "../api/queryKeys";
import {
  type OverviewRow,
  type ScheduleOverview,
  ScheduleActionError,
  type SchedulesApi,
  mayRunNow,
  schedulesApi,
} from "../api/schedules";
import { wuiAddress } from "../api/wui";
import { AppTag } from "../components/AppTag";
import { useDialog } from "../components/Dialog";
import { ScheduleTimeModal } from "../components/ScheduleTimeModal";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useApps } from "../hooks/useResources";
import { describeSchedule } from "../lib/describeSchedule";
import { type MsgKey, useT } from "../lib/i18n";
import { type ScheduleSort, useScheduleOverviewPrefs } from "../lib/scheduleOverviewPrefs";
import { fullTime, whenText } from "../lib/scheduleTime";
import { type ViewerClock, useViewerClock } from "../lib/viewerClock";

/** The last-run states that need somebody: it failed, it was stopped, or it is
 * waiting for a review — the "needs attention" sort puts these first. */
const ATTENTION = new Set(["error", "cancelled", "awaiting_human"]);

/** A run that has not finished yet — while one is on the page, it refreshes
 * often enough to see it finish. */
const IN_FLIGHT = new Set(["pending", "running"]);

const ITEM_SCHEDULES = "/.workflows/schedules.json";

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

/** The folder a page's schedules file sits in; "" for the item's own. */
const folderOf = (row: Pick<OverviewRow, "path">) =>
  row.path === ITEM_SCHEDULES ? "" : row.path.slice(0, row.path.lastIndexOf("/"));

/** Where a schedule lives, in words: the item, and the page when it is a
 * page's — by the page's title, its folder only when it has none. */
export function placeOf(row: OverviewRow, t: ReturnType<typeof useT>): string {
  const item = row.item_title || row.item_id;
  const folder = folderOf(row);
  if (!folder) return item;
  return `${item} · ${t("scheduleOverview.page", { page: row.page_title || folder })}`;
}

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
          {shown.length > 0 ? <ScheduleTable groups={groups} client={client} /> : null}
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
}: {
  groups: Map<string, OverviewRow[]>;
  client: SchedulesApi;
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
            <th scope="col">{t("scheduleOverview.col.actions")}</th>
          </tr>
        </thead>
        {[...groups].map(([slug, rows]) => (
          <tbody key={slug || "all"}>
            {slug ? (
              <tr className="schedule-group">
                <th scope="colgroup" colSpan={6}>
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
              />
            ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}

/** One row and its actions — per row, so one row's pending press does not
 * hold every other row's buttons (the WUI overview's `useRemove` pattern). */
function ScheduleRowView({
  row,
  client,
  clock,
}: {
  row: OverviewRow;
  client: SchedulesApi;
  clock: ViewerClock;
}) {
  const t = useT();
  const qc = useQueryClient();
  const dialog = useDialog();
  const [editing, setEditing] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  // The run Run now just started, until the row's last run IS that run — then
  // the last-run cell says everything this note would.
  const [started, setStarted] = useState<string | null>(null);
  // A row the sweep refuses has no identity; Remove finds it by its value.
  const ref = {
    path: row.path,
    trigger_id: row.trigger_id,
    raw: row.trigger_id ? undefined : row.raw,
    index: row.trigger_id ? undefined : row.index,
  };
  // Both entrances' caches: the item's panel lists this row too (decision 9),
  // and the file tree shows the file just rewritten.
  const refresh = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: qk.schedulesOverview }),
      qc.invalidateQueries({ queryKey: qk.itemSchedules(row.slug, row.item_id) }),
      qc.invalidateQueries({ queryKey: qk.files(row.item_id) }),
      // Run now may have just made the schedule's own chat.
      qc.invalidateQueries({ queryKey: qk.itemChats(row.slug, row.item_id) }),
    ]);
  const fail = (e: unknown) => setFailed(e instanceof ScheduleActionError ? e.message : String(e));

  const runNow = useMutation({
    mutationFn: () => client.runNow(row.slug, row.item_id, ref),
    onSuccess: (runId) => {
      setStarted(runId || "?");
      return refresh();
    },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: () => client.remove(row.slug, row.item_id, ref),
    onSuccess: refresh,
    onError: fail,
  });
  const period = describeSchedule(row, clock, t);
  const workflow = row.run_title || row.run || "?";
  const askRemove = async () => {
    const choice = await dialog.confirm({
      title: t("schedules.removeTitle"),
      body: t("scheduleOverview.removeConfirm", {
        place: placeOf(row, t),
        period: period.text,
        workflow,
      }),
      // The safe answer first: the destructive one is never what a stray
      // Enter or the first focus lands on.
      actions: [
        { id: "cancel", label: t("schedules.cancel") },
        { id: "remove", label: t("schedules.remove"), variant: "danger" },
      ],
    });
    if (choice === "remove") remove.mutate();
  };

  const itemHref = `/a/${encodeURIComponent(row.slug)}/${encodeURIComponent(row.item_id)}`;
  const chatHref = `${itemHref}?chat=${encodeURIComponent(row.trigger_id)}&from=schedules`;
  const folder = folderOf(row);
  // A row the sweep refuses has no identity: it has no time to move and
  // nothing that would run, so only Remove (by its value) applies.
  const identified = row.trigger_id !== "";
  const mayRun = mayRunNow(row, row.can_run);
  const last = row.last_run;
  const lastAt = last ? (last.ended ?? last.started) : null;
  const showStarted = started !== null && last?.run_id !== started;

  return (
    <tr data-testid={`schedule-${row.item_id}${row.path}#${row.index}`}>
      <td>
        <Link to={itemHref}>{row.item_title || row.item_id}</Link>
        <div className="detail">
          {folder ? (
            row.page_path ? (
              <a href={wuiAddress(row.slug, row.item_id, row.page_path)} target="_blank" rel="noreferrer">
                {t("scheduleOverview.page", { page: row.page_title || folder })}
              </a>
            ) : (
              t("scheduleOverview.page", { page: row.page_title || folder })
            )
          ) : (
            t("scheduleOverview.itemOwn")
          )}
        </div>
      </td>
      <td title={row.run_title && row.run !== row.run_title ? row.run : undefined}>{row.run_title || row.run || "—"}</td>
      <td className="schedule-when" title={period.set || undefined}>
        {period.text}
      </td>
      <td className={row.runnable ? "schedule-next" : "schedule-next schedule-problem"}>
        <NextCell row={row} clock={clock} />
      </td>
      <td className="schedule-last">
        {last ? (
          <Link to={chatHref} title={lastAt ? fullTime(lastAt, clock.viewer, t) : undefined}>
            <span className="schedule-status" data-status={last.status}>
              {t(`scheduleOverview.status.${last.status}` as MsgKey)}
            </span>
            {last.by_hand ? <span className="schedule-tag">{t("scheduleOverview.byHand")}</span> : null}{" "}
            {lastAt ? whenText(lastAt, clock.now, clock.viewer, t) : null}
          </Link>
        ) : (
          t("scheduleOverview.never")
        )}
      </td>
      <td>
        <div className="schedule-actions">
          {mayRun ? (
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              disabled={runNow.isPending}
              onClick={() => {
                setFailed(null);
                runNow.mutate();
              }}
            >
              {t("schedules.runNow")}
            </button>
          ) : null}
          {row.can_edit && identified ? (
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              onClick={() => {
                setFailed(null);
                setEditing(true);
              }}
            >
              {t("schedules.editTime")}
            </button>
          ) : null}
          {row.can_edit ? (
            <button
              type="button"
              className="btn"
              data-variant="danger"
              data-size="sm"
              disabled={remove.isPending}
              aria-label={`${t("schedules.remove")} ${period.text} → ${workflow}`}
              onClick={() => {
                setFailed(null);
                void askRemove();
              }}
            >
              {t("schedules.remove")}
            </button>
          ) : null}
        </div>
        {showStarted ? (
          <div className="detail" role="status">
            {t("schedules.started")} · <Link to={chatHref}>{t("scheduleOverview.seeRun")}</Link>
          </div>
        ) : null}
        {failed ? (
          <div className="detail schedule-problem" role="alert">
            {failed}
          </div>
        ) : null}
        {editing ? (
          <ScheduleTimeModal
            raw={row.raw}
            nextMs={row.next_ms}
            rowRef={ref}
            onSave={(r, time) => client.editTime(row.slug, row.item_id, r, time)}
            onSaved={() => {
              setEditing(false);
              void refresh();
            }}
            onClose={() => setEditing(false)}
          />
        ) : null}
      </td>
    </tr>
  );
}

/** When it runs next, or why it will not — the same reasons the item's panel
 * gives. */
function NextCell({ row, clock }: { row: OverviewRow; clock: ViewerClock }) {
  const t = useT();
  if (row.problems.length > 0) return <>{`${t("schedules.invalidRow")} ${row.problems.join(" ")}`}</>;
  if (!row.known) return <>{t("schedules.unknownWorkflow")}</>;
  if (row.run_problem) return <>{`${t("schedules.brokenWorkflow")} ${row.run_problem}`}</>;
  if (!row.runnable || row.next_ms === null) return <>—</>;
  if (row.due_now) return <>{t("scheduleOverview.nextSweep")}</>;
  return <span title={fullTime(row.next_ms, clock.viewer, t)}>{whenText(row.next_ms, clock.now, clock.viewer, t)}</span>;
}
