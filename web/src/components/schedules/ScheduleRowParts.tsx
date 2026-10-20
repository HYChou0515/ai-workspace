/**
 * One schedule row's parts — where it lives, when it runs next, how it last
 * went, who it runs as, and the acts on it — shared by the schedules overview
 * (a table) and the item's Workflows panel (a list).
 *
 * The two screens list the SAME rows (`GET /schedules`, and its narrowing to
 * one item), so they must say each thing in the same words with the same
 * buttons (`docs/plan-schedule-run-as.md` G9; NN/g, *Consistency and
 * Standards*). They differ only in arrangement: a wide table there, a stacked
 * card here. Anything a row SAYS lives in this file, never in either screen.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { qk } from "../../api/queryKeys";
import {
  type OverviewRow,
  type RowRef,
  ScheduleActionError,
  type SchedulesApi,
  mayRunNow,
} from "../../api/schedules";
import { wuiAddress } from "../../api/wui";
import { describeSchedule } from "../../lib/describeSchedule";
import { executionPath, OPEN_PARAM } from "../../lib/executionTab";
import { type MsgKey, useT } from "../../lib/i18n";
import { fullTime, whenText } from "../../lib/scheduleTime";
import type { ViewerClock } from "../../lib/viewerClock";
import { useDialog } from "../Dialog";
import { ScheduleTimeModal } from "../ScheduleTimeModal";
import { RunsAs } from "./RunsAs";

export const ITEM_SCHEDULES = "/.workflows/schedules.json";

/** The folder a page's schedules file sits in; "" for the item's own. */
export const folderOf = (row: Pick<OverviewRow, "path">) =>
  row.path === ITEM_SCHEDULES ? "" : row.path.slice(0, row.path.lastIndexOf("/"));

/** Where a schedule lives, in words: the item, and the page when it is a
 * page's — by the page's title, its folder only when it has none. */
export function placeOf(row: OverviewRow, t: ReturnType<typeof useT>): string {
  const item = row.item_title || row.item_id;
  const folder = folderOf(row);
  if (!folder) return item;
  return `${item} · ${t("scheduleOverview.page", { page: row.page_title || folder })}`;
}

/** The workflow a row runs, by the name people gave it. */
export const workflowName = (row: Pick<OverviewRow, "run" | "run_title">) => row.run_title || row.run || "—";

const itemHrefOf = (row: OverviewRow) =>
  `/a/${encodeURIComponent(row.slug)}/${encodeURIComponent(row.item_id)}`;

/** An execution opens as a tab in its item (plan-workflow-out-of-chat P11). */
const runHrefOf = (row: OverviewRow, runId: string) =>
  runId ? `${itemHrefOf(row)}?${OPEN_PARAM}=${encodeURIComponent(executionPath(runId))}` : itemHrefOf(row);

/** Every cache that lists this row: the overview, the item's panel, the file
 * tree (the file was just rewritten), the item's chats (Run now may have made
 * the schedule's own), its executions. */
export function useRefreshSchedules(row: Pick<OverviewRow, "slug" | "item_id">) {
  const qc = useQueryClient();
  return () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: qk.schedulesOverview }),
      qc.invalidateQueries({ queryKey: qk.itemSchedules(row.slug, row.item_id) }),
      qc.invalidateQueries({ queryKey: qk.files(row.item_id) }),
      qc.invalidateQueries({ queryKey: qk.itemChats(row.slug, row.item_id) }),
      qc.invalidateQueries({ queryKey: qk.workflowRuns(row.slug, row.item_id) }),
    ]);
}

/** Where it lives. ``withItem``: name the item too (the overview spans items;
 * the item's own panel does not need to say which item it is). */
export function ScheduleWhere({ row, withItem }: { row: OverviewRow; withItem: boolean }) {
  const t = useT();
  const folder = folderOf(row);
  const label = folder ? t("scheduleOverview.page", { page: row.page_title || folder }) : t("scheduleOverview.itemOwn");
  return (
    <>
      {withItem ? <Link to={itemHrefOf(row)}>{row.item_title || row.item_id}</Link> : null}
      <div className="detail">
        {folder && row.page_path ? (
          <a href={wuiAddress(row.slug, row.item_id, row.page_path)} target="_blank" rel="noreferrer">
            {label}
          </a>
        ) : (
          label
        )}
      </div>
    </>
  );
}

/** When it runs next, or why it will not. */
export function ScheduleNext({ row, clock }: { row: OverviewRow; clock: ViewerClock }) {
  const t = useT();
  if (row.problems.length > 0) return <>{`${t("schedules.invalidRow")} ${row.problems.join(" ")}`}</>;
  if (!row.known) return <>{t("schedules.unknownWorkflow")}</>;
  if (row.run_problem) return <>{`${t("schedules.brokenWorkflow")} ${row.run_problem}`}</>;
  if (!row.runnable || row.next_ms === null) return <>—</>;
  if (row.due_now) return <>{t("scheduleOverview.nextSweep")}</>;
  return (
    <span title={fullTime(row.next_ms, clock.viewer, t)}>
      {whenText(row.next_ms, clock.now, clock.viewer, t, "future")}
    </span>
  );
}

/** How it last went — a link to that execution — or that it never ran. */
export function ScheduleLast({ row, clock }: { row: OverviewRow; clock: ViewerClock }) {
  const t = useT();
  const last = row.last_run;
  if (!last) return <>{t("scheduleOverview.never")}</>;
  const lastAt = last.ended ?? last.started;
  return (
    <Link to={runHrefOf(row, last.run_id)} title={lastAt ? fullTime(lastAt, clock.viewer, t) : undefined}>
      <span className="schedule-status" data-status={last.status}>
        {t(`scheduleOverview.status.${last.status}` as MsgKey)}
      </span>
      {last.by_hand ? <span className="schedule-tag">{t("scheduleOverview.byHand")}</span> : null}{" "}
      {lastAt ? whenText(lastAt, clock.now, clock.viewer, t, "past") : null}
    </Link>
  );
}

/** Who it runs as, and why — with "Run as me" / "Choose…". A row the sweep
 * refuses has no identity, so nobody can be bound to it. */
export function ScheduleRunsAs({ row, canAssign }: { row: OverviewRow; canAssign: boolean }) {
  const refresh = useRefreshSchedules(row);
  if (!row.trigger_id) return <>—</>;
  return (
    <RunsAs
      slug={row.slug}
      itemId={row.item_id}
      path={row.path}
      triggerId={row.trigger_id}
      runsAs={{ who: row.runs_as, reason: row.runs_as_reason, by: row.runs_as_by, mine: row.runs_as_mine }}
      canRun={row.can_run}
      canAssign={canAssign}
      onChanged={() => void refresh()}
    />
  );
}

/** The acts on a row — Run now, change its time, remove it — with what came
 * of the last one. Per row, so one row's pending press does not hold every
 * other row's buttons. */
export function ScheduleActions({
  row,
  client,
  clock,
}: {
  row: OverviewRow;
  client: SchedulesApi;
  clock: ViewerClock;
}) {
  const t = useT();
  const dialog = useDialog();
  const refresh = useRefreshSchedules(row);
  const [editing, setEditing] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  // After Run now: the run the row showed as its last when it was pressed.
  // The note stays until the row shows any other run — that one is the press's
  // (or a fire that overtook it), and the last-run cell then says everything
  // the note would. Not "until it shows the run id the press got back": a
  // reply without one, or an overtaking fire, left the note up for good.
  const [started, setStarted] = useState<{ before: string | null; runId: string } | null>(null);
  // A row the sweep refuses has no identity; Remove finds it by its value.
  const ref: RowRef = {
    path: row.path,
    trigger_id: row.trigger_id,
    raw: row.trigger_id ? undefined : row.raw,
    index: row.trigger_id ? undefined : row.index,
  };
  const fail = (e: unknown) => setFailed(e instanceof ScheduleActionError ? e.message : String(e));
  const runNow = useMutation({
    mutationFn: (_before: string | null) => client.runNow(row.slug, row.item_id, ref),
    onSuccess: (runId, before) => {
      setStarted({ before, runId });
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
  const workflow = workflowName(row);
  const askRemove = async () => {
    const choice = await dialog.confirm({
      title: t("schedules.removeTitle"),
      body: t("scheduleOverview.removeConfirm", { place: placeOf(row, t), period: period.text, workflow }),
      // The safe answer first: the destructive one is never what a stray
      // Enter or the first focus lands on.
      actions: [
        { id: "cancel", label: t("schedules.cancel") },
        { id: "remove", label: t("schedules.remove"), variant: "danger" },
      ],
    });
    if (choice === "remove") remove.mutate();
  };
  // A row the sweep refuses has no identity: it has no time to move and
  // nothing that would run, so only Remove (by its value) applies.
  const identified = row.trigger_id !== "";
  const showStarted = started !== null && (row.last_run?.run_id ?? null) === started.before;

  return (
    <>
      <div className="schedule-actions">
        {mayRunNow(row, row.can_run) ? (
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            disabled={runNow.isPending}
            onClick={() => {
              setFailed(null);
              runNow.mutate(row.last_run?.run_id ?? null);
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
            aria-label={`${t("schedules.remove")} ${placeOf(row, t)} · ${period.text} → ${workflow}`}
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
          {t("schedules.started")} · <Link to={runHrefOf(row, started?.runId ?? "")}>{t("scheduleOverview.seeRun")}</Link>
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
    </>
  );
}
