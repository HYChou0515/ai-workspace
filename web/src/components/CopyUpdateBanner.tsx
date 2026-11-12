/**
 * A WUI copy that is behind its publication says so (`docs/plan-wui-copy-update.md`).
 *
 * Drawn under the reader page's identity strip and under the workspace's title
 * bar (D3) — the same strip, and the same CSS, as the skill-update reminder
 * (#1004): Material 3 *Banners* — below the top bar, a message and text
 * buttons, dismissed by an action that addresses it ("Remind me later"), never
 * a lone ×. The note's first line rides in the message (D6) because it is what
 * decides whether to take it; 看說明 opens all of them.
 *
 * Taking it (D1) makes a NEW copy; this one stays, renamed （舊版）. A shared
 * copy is not copied again (D2): 前往新版 opens the publication, which lands in
 * the current shared copy. Without room (D5) it says by how much, and offers
 * deleting this copy in its place — confirmed first, in words that say it is
 * permanent, because an item delete is.
 *
 * Later / skip are #1004's own (`skillUpdateReminders`): the same 7 days, the
 * same "skip from the second reminder on", per person + copy + publish — keyed
 * `wui-copy:<item>` so a skill and a copy can never share an answer.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { type CopyUpdateApi, copyUpdateApi } from "../api/publish";
import { encodePath } from "../api/refPath";
import { useCurrentUserState } from "../hooks/useCurrentUser";
import { formatBytes } from "../lib/bytes";
import { useT } from "../lib/i18n";
import { onRemindersChanged, reminderOf, remindLater, skipVersion } from "../lib/skillUpdateReminders";
import { firstLine } from "./SkillUpdateBanner";
import { useDialog } from "./Dialog";

const entryOf = (itemId: string) => `wui-copy:${itemId}`;

function shortDate(ms: number): string {
  const d = new Date(ms);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export function CopyUpdateBanner({
  slug,
  itemId,
  where,
  client = copyUpdateApi,
}: {
  slug: string;
  itemId: string;
  /** Which surface it is on — where a new copy opens: its page, or its workspace. */
  where: "page" | "workspace";
  client?: CopyUpdateApi;
}) {
  const t = useT();
  const qc = useQueryClient();
  const dialog = useDialog();
  const navigate = useNavigate();
  const me = useCurrentUserState();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Re-read the stored answers whenever a Later or a Skip is recorded — here
  // or in another tab.
  const [, setTick] = useState(0);
  useEffect(() => onRemindersChanged(() => setTick((n) => n + 1)), []);

  const { data } = useQuery({
    queryKey: ["copyUpdate", slug, itemId],
    queryFn: () => client.status(slug, itemId),
    // Extra, never in the way: a failed check says nothing.
    retry: false,
  });

  // Judged only once the user is KNOWN (#997): a placeholder id would re-show
  // a reminder its person already answered.
  if (!data?.due || !me.ready) return null;
  const reminder = reminderOf(me.id, entryOf(itemId), data.published_at, Date.now());
  if (!reminder.due) return null;

  const date = shortDate(data.published_at);
  const gist = firstLine(data.notes.at(-1)?.text ?? "");
  const shared = data.sharing === "shared";
  const message = shared
    ? t("copyUpdate.shared", { date })
    : gist
      ? t("copyUpdate.messageNote", { date, note: gist })
      : t("copyUpdate.message", { date });
  const canReplace =
    !data.fits && data.free_if_replaced !== null && data.needs <= data.free_if_replaced;

  const take = async (replace: boolean) => {
    setBusy(true);
    setError("");
    try {
      const made = await client.take(slug, itemId, { replace });
      await qc.invalidateQueries({ queryKey: ["copyUpdate", slug, itemId] });
      navigate(
        where === "page"
          ? `/w/${encodeURIComponent(made.slug)}/${encodeURIComponent(made.item_id)}/${encodePath(data.path)}`
          : `/a/${encodeURIComponent(made.slug)}/${encodeURIComponent(made.item_id)}`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      // What is true now — the room may have changed under it.
      await qc.invalidateQueries({ queryKey: ["copyUpdate", slug, itemId] });
    } finally {
      setBusy(false);
    }
  };

  const replace = async () => {
    const choice = await dialog.confirm({
      title: t("copyUpdate.replaceTitle"),
      body: t("copyUpdate.replaceBody"),
      actions: [
        { id: "cancel", label: t("copyUpdate.cancel") },
        { id: "replace", label: t("copyUpdate.replace"), variant: "danger" },
      ],
    });
    if (choice === "replace") await take(true);
  };

  const goTo = () =>
    navigate(
      `/open/${encodeURIComponent(slug)}/${encodeURIComponent(data.source_item_id)}/${encodePath(data.path)}`,
    );

  return (
    <section className="skill-update-banner" aria-label={t("copyUpdate.label")}>
      <div className="skill-update-banner__line">
        <span className="skill-update-banner__text">{message}</span>
        {shared ? (
          <button type="button" className="skill-update-banner__action" onClick={goTo}>
            {t("copyUpdate.goTo")}
          </button>
        ) : data.fits ? (
          <button
            type="button"
            className="skill-update-banner__action"
            title={t("copyUpdate.take.tip")}
            disabled={busy}
            onClick={() => void take(false)}
          >
            {t("copyUpdate.take")}
          </button>
        ) : null}
        {data.notes.length > 0 ? (
          <button
            type="button"
            className="skill-update-banner__action"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {t("copyUpdate.notes")}
          </button>
        ) : null}
        <button
          type="button"
          className="skill-update-banner__action"
          disabled={busy}
          onClick={() => remindLater(me.id, entryOf(itemId), data.published_at, Date.now())}
        >
          {t("skills.updates.later")}
        </button>
        {reminder.canSkip ? (
          <button
            type="button"
            className="skill-update-banner__action"
            disabled={busy}
            onClick={() => skipVersion(me.id, entryOf(itemId), data.published_at)}
          >
            {t("skills.updates.skip")}
          </button>
        ) : null}
      </div>
      {!shared && !data.fits ? (
        <div className="skill-update-banner__line" role="note">
          <span className="skill-update-banner__text">
            {t("copyUpdate.noRoom", { needs: formatBytes(data.needs), free: formatBytes(Math.max(0, data.free ?? 0)) })}{" "}
            {canReplace ? null : t("copyUpdate.noRoomAtAll")}
          </span>
          {canReplace ? (
            <button
              type="button"
              className="skill-update-banner__action"
              disabled={busy}
              onClick={() => void replace()}
            >
              {t("copyUpdate.replace")}
            </button>
          ) : null}
        </div>
      ) : null}
      <div className="skill-update-banner__status" role="status">
        {busy ? <p>{t("copyUpdate.taking")}</p> : null}
      </div>
      <div className="skill-update-banner__error" role="alert">
        {error ? <p>{t("copyUpdate.failed", { error })}</p> : null}
      </div>
      {open ? (
        <ul className="skill-update-banner__notes">
          {data.notes.map((n) => (
            <li key={n.at} style={{ whiteSpace: "pre-wrap" }}>
              <strong>{shortDate(n.at)}</strong> {n.text}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
