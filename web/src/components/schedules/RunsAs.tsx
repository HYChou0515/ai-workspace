/**
 * Who a schedule runs as, and why (`docs/plan-schedule-run-as.md` G8).
 *
 * ONE control for every place a schedule is listed — the schedules overview,
 * the item's Workflows panel, a page's own schedules list — so the same fact is
 * said in the same words with the same buttons wherever a person meets it
 * (G9; NN/g, *Consistency and Standards*: "use the same patterns everywhere").
 *
 * Running as someone uses their own sign-ins and values, an act with
 * consequences for them, so it is always said, never implied (NN/g, *Visibility
 * of System Status*): who, and why it is them — they chose it, they wrote the
 * row, or an administrator named them.
 */
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { type ScheduleBindingsClient, scheduleBindingsApi } from "../../api/scheduleBindings";
import type { RunsAsReason } from "../../api/schedules";
import { useT } from "../../lib/i18n";
import { pxToRem } from "../../lib/pxToRem";
import { useDialog } from "../Dialog";
import { ModalShell } from "../ModalShell";
import { UserPicker } from "../UserPicker";

export type RunsAsFacts = {
  /** Who it runs as; `""` = nobody. */
  who: string;
  reason: RunsAsReason;
  /** The administrator who named them, for `admin`. */
  by: string;
  /** Whether that is the viewer. */
  mine: boolean;
};

/** The sentence: "runs as X (why)", or "nobody's own values". */
export function runsAsText(r: RunsAsFacts, t: ReturnType<typeof useT>): string {
  if (!r.who) return t("sched.runsAsNobody");
  const main = r.mine ? t("sched.runsAsYou") : t("sched.runsAs", { who: r.who });
  const why =
    r.reason === "admin"
      ? t("sched.why.admin", { by: r.by })
      : r.reason === "author"
        ? t(r.mine ? "sched.why.authorYou" : "sched.why.author")
        : t(r.mine ? "sched.why.selfYou" : "sched.why.self");
  return t("sched.runsAsWhy", { main, why });
}

export function RunsAs({
  slug,
  itemId,
  path,
  triggerId,
  runsAs,
  canRun,
  canAssign,
  client = scheduleBindingsApi,
  onChanged,
}: {
  slug: string;
  itemId: string;
  /** The schedules file the row lives in. */
  path: string;
  triggerId: string;
  runsAs: RunsAsFacts;
  /** May the viewer run work in the item (`execute`) — "Run as me". */
  canRun: boolean;
  /** Is the viewer an administrator — "Choose…" someone else. */
  canAssign: boolean;
  client?: ScheduleBindingsClient;
  /** After a change landed — the caller refetches its rows. */
  onChanged?: () => void;
}) {
  const t = useT();
  const dialog = useDialog();
  const [problem, setProblem] = useState<string | null>(null);
  const [choosing, setChoosing] = useState(false);
  const change = useMutation({
    mutationFn: (act: { kind: "bind" } | { kind: "unbind" } | { kind: "assign"; user: string }) =>
      act.kind === "assign"
        ? client.assign(slug, itemId, path, triggerId, act.user)
        : act.kind === "bind"
          ? client.bind(slug, itemId, path, triggerId)
          : client.unbind(slug, itemId, path, triggerId),
    onMutate: () => setProblem(null),
    // Refused or not, the list is read again: a refusal is usually "this row
    // changed meanwhile", and the list should then show what IS there.
    onSettled: () => onChanged?.(),
    onError: (err) => {
      const why = (err as { detail?: unknown })?.detail;
      setProblem(t("sched.failed", { why: typeof why === "string" ? why : String((err as Error).message) }));
    },
  });

  const runAsMe = async () => {
    if (runsAs.who) {
      // Taking it over from someone: they lose it, and are told — so ask.
      const choice = await dialog.confirm({
        title: t("sched.replaceTitle"),
        body: t("sched.replaceBody", { who: runsAs.who }),
        actions: [
          { id: "cancel", label: t("env.cancel") },
          { id: "replace", label: t("sched.replaceMe"), variant: "primary" },
        ],
      });
      if (choice !== "replace") return;
    }
    change.mutate({ kind: "bind" });
  };

  return (
    <div style={{ display: "grid", gap: 4 }}>
      <span data-testid="runs-as" style={{ fontSize: pxToRem(12) }}>
        {runsAsText(runsAs, t)}
      </span>
      <span style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {runsAs.mine ? (
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            disabled={change.isPending}
            onClick={() => change.mutate({ kind: "unbind" })}
          >
            {t("sched.unbind")}
          </button>
        ) : canRun ? (
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            disabled={change.isPending}
            onClick={() => void runAsMe()}
          >
            {runsAs.who ? t("sched.replaceMe") : t("sched.bindMe")}
          </button>
        ) : null}
        {canAssign ? (
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            disabled={change.isPending}
            onClick={() => setChoosing(true)}
          >
            {t("sched.assign")}
          </button>
        ) : null}
      </span>
      {problem ? (
        <span role="alert" style={{ fontSize: pxToRem(11), color: "var(--err)" }}>
          {problem}
        </span>
      ) : null}
      {choosing ? (
        // Nothing typed to lose: picking a person IS the act, so a stray click
        // beside the panel may close it (#779 — a picker that applies live).
        <ModalShell
          onClose={() => setChoosing(false)}
          ariaLabel={t("sched.assignTitle")}
          width={360}
          maxWidth="92vw"
          closeOnBackdrop
          panelStyle={{ padding: 16, display: "grid", gap: 8 }}
        >
          <strong>{t("sched.assignTitle")}</strong>
          <p style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
            {t("sched.assignBody")}
          </p>
          <UserPicker
            selected={runsAs.who ? [runsAs.who] : []}
            onToggle={(user) => {
              setChoosing(false);
              change.mutate({ kind: "assign", user });
            }}
            placeholder={t("sched.assignSearch")}
          />
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            style={{ justifySelf: "end" }}
            onClick={() => setChoosing(false)}
          >
            {t("env.cancel")}
          </button>
        </ModalShell>
      ) : null}
    </div>
  );
}
