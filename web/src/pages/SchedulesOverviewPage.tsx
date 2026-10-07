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
 * "Last run" opens the schedule's own conversation — every run of one schedule
 * writes there — through the item's `?chat=` deep link.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { qk } from "../api/queryKeys";
import {
  type OverviewRow,
  ScheduleActionError,
  type SchedulesApi,
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

/** The last-run states that need somebody: it failed, it was stopped, or it is
 * waiting for a review — the "needs attention" sort puts these first. */
const ATTENTION = new Set(["error", "cancelled", "awaiting_human"]);

const ITEM_SCHEDULES = "/.workflows/schedules.json";

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
  const needs = (r: OverviewRow) => ATTENTION.has(r.last_run?.status ?? "");
  return [...rows].sort((a, b) => {
    if (needs(a) !== needs(b)) return needs(a) ? -1 : 1;
    if (needs(a)) return when(b) - when(a);
    return byNext(a, b);
  });
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
          {[...groups].map(([slug, rows]) =>
            // Grouped: a region per App, named by its heading — the App's own
            // name, as on the WUI overview — so a group can be landed on.
            slug ? (
              <section key={slug} aria-labelledby={`schedules-app-${slug}`}>
                <h2 id={`schedules-app-${slug}`}>
                  <AppTag slug={slug} />
                </h2>
                <ScheduleTable rows={rows} client={client} />
              </section>
            ) : (
              <ScheduleTable key="all" rows={rows} client={client} />
            ),
          )}
        </>
      )}
    </div>
  );
}

function ScheduleTable({ rows, client }: { rows: OverviewRow[]; client: SchedulesApi }) {
  const t = useT();
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
        <tbody>
          {rows.map((row) => (
            <ScheduleRowView
              key={`${row.item_id}${row.path}#${row.index}`}
              row={row}
              client={client}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One row and its actions — per row, so one row's pending press does not
 * hold every other row's buttons (the WUI overview's `useRemove` pattern). */
function ScheduleRowView({ row, client }: { row: OverviewRow; client: SchedulesApi }) {
  const t = useT();
  const qc = useQueryClient();
  const dialog = useDialog();
  const [editing, setEditing] = useState(false);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  // A row the sweep refuses has no identity; Remove finds it by its value.
  const ref = {
    path: row.path,
    trigger_id: row.trigger_id,
    raw: row.trigger_id ? undefined : row.raw,
  };
  const refresh = () => qc.invalidateQueries({ queryKey: qk.schedulesOverview });
  const fail = (e: unknown) =>
    setSaid({ ok: false, text: e instanceof ScheduleActionError ? e.message : String(e) });

  const runNow = useMutation({
    mutationFn: () => client.runNow(row.slug, row.item_id, ref),
    onSuccess: () => {
      setSaid({ ok: true, text: t("schedules.started") });
      return refresh();
    },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: () => client.remove(row.slug, row.item_id, ref),
    onSuccess: refresh,
    onError: fail,
  });
  const what = `${describeSchedule(row.raw, t)} → ${row.run || "?"}`;
  const askRemove = async () => {
    const choice = await dialog.confirm({
      title: t("schedules.removeTitle"),
      body: t("schedules.removeConfirm", { what }),
      actions: [
        { id: "remove", label: t("schedules.remove"), variant: "danger" },
        { id: "cancel", label: t("schedules.cancel") },
      ],
    });
    if (choice === "remove") remove.mutate();
  };

  const itemHref = `/a/${encodeURIComponent(row.slug)}/${encodeURIComponent(row.item_id)}`;
  const folder = row.path === ITEM_SCHEDULES ? "" : row.path.slice(0, row.path.lastIndexOf("/"));
  // A row the sweep refuses has no identity: it has no time to move and
  // nothing that would run, so only Remove (by its value) applies.
  const identified = row.trigger_id !== "";
  const mayRun = row.can_run && identified && row.known && !row.run_problem;

  return (
    <tr data-testid={`schedule-${row.item_id}${row.path}#${row.index}`}>
      <td>
        <Link to={itemHref}>{row.item_title || row.item_id}</Link>
        {folder ? (
          <div className="detail">
            {row.page_path ? (
              <a href={wuiAddress(row.slug, row.item_id, row.page_path)} target="_blank" rel="noreferrer">
                {t("scheduleOverview.page", { folder })}
              </a>
            ) : (
              t("scheduleOverview.page", { folder })
            )}
          </div>
        ) : null}
      </td>
      <td>{row.run || "—"}</td>
      <td className="schedule-when">{describeSchedule(row.raw, t)}</td>
      <td className={row.runnable ? "schedule-next" : "schedule-next schedule-problem"}>
        <NextCell row={row} />
      </td>
      <td className="schedule-last">
        {row.last_run ? (
          <Link to={`${itemHref}?chat=${encodeURIComponent(row.trigger_id)}`}>
            <span className="schedule-status" data-status={row.last_run.status}>
              {t(`scheduleOverview.status.${row.last_run.status}` as MsgKey)}
            </span>{" "}
            {timeOf(row.last_run.ended ?? row.last_run.started)}
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
                setSaid(null);
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
                setSaid(null);
                setEditing(true);
              }}
            >
              {t("schedules.editTime")}
            </button>
          ) : null}
          {row.can_edit ? (
            <>
              <button
                type="button"
                className="btn"
                data-variant="secondary"
                data-size="sm"
                disabled={remove.isPending}
                aria-label={`${t("schedules.remove")} ${what}`}
                onClick={() => {
                  setSaid(null);
                  void askRemove();
                }}
              >
                {t("schedules.remove")}
              </button>
            </>
          ) : null}
        </div>
        {said ? (
          <div className={said.ok ? "detail" : "detail schedule-problem"} role={said.ok ? "status" : "alert"}>
            {said.text}
          </div>
        ) : null}
        {editing ? (
          <ScheduleTimeModal
            raw={row.raw}
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
function NextCell({ row }: { row: OverviewRow }) {
  const t = useT();
  if (row.problems.length > 0) return <>{`${t("schedules.invalidRow")} ${row.problems.join(" ")}`}</>;
  if (!row.known) return <>{t("schedules.unknownWorkflow")}</>;
  if (row.run_problem) return <>{`${t("schedules.brokenWorkflow")} ${row.run_problem}`}</>;
  if (!row.runnable) return <>—</>;
  if (row.due_now) return <>{t("scheduleOverview.nextSweep")}</>;
  return <>{`${row.next_at} ${row.tz}`}</>;
}

function timeOf(ms: number | null): string {
  return ms ? new Date(ms).toLocaleString() : "";
}
