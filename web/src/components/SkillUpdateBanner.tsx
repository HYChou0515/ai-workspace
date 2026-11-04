/**
 * The skill-update reminder ON the item, above the composer — seen on opening
 * it, not three clicks into the Skills panel (the user, on the first build of
 * #1004: 「他要打開面板才看得到 -> 不會有人看到」). One banner however many
 * skills are due, worded for one or several.
 *
 * Material 3 Banners: one at a time; at most two text buttons; the dismissing
 * action addresses the message ("Remind me later"), never a lone ×; it stays
 * until acted on. NN/g *Indicators, Validations, and Notifications*: a
 * non-urgent message the person should act on is a non-modal notification.
 * "看更新說明" opens the notes in place — the same rows as the panel's
 * (`SkillUpdates`), with Update / Later / Skip there.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { ApiClient } from "../api/types";
import { useCurrentUserState } from "../hooks/useCurrentUser";
import { useDueSkillUpdates } from "../hooks/useSkillUpdateMark";
import { useT } from "../lib/i18n";
import { remindLater } from "../lib/skillUpdateReminders";
import { SkillUpdates } from "./SkillUpdates";

export function SkillUpdateBanner({
  slug,
  itemId,
  canUpdate,
  client = api,
}: {
  slug: string;
  itemId: string;
  /** May this viewer change the item's copies (`edit_content`, D4)? */
  canUpdate: boolean;
  client?: Pick<ApiClient, "getItemSkills" | "refreshItemSkill">;
}) {
  const t = useT();
  const qc = useQueryClient();
  const { id: me } = useCurrentUserState();
  const due = useDueSkillUpdates(slug, itemId, client);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (due.length === 0) return null;

  const update = async (name: string) => {
    setError(null);
    try {
      await client.refreshItemSkill(slug, itemId, name, { force: false });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }
    await qc.invalidateQueries({ queryKey: qk.itemSkills(slug, itemId) });
  };

  const first = due[0];
  const message =
    due.length === 1
      ? t("skills.banner.one", { name: first.name, version: first.updates?.[0]?.version ?? 0 })
      : t("skills.banner.many", { n: due.length });

  return (
    <section className="skill-update-banner" aria-label={t("skills.banner.label")}>
      <div className="skill-update-banner__line">
        <span className="skill-update-banner__text">{message}</span>
        <button
          type="button"
          className="skill-update-banner__action"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {t("skills.updates.show")}
        </button>
        <button
          type="button"
          className="skill-update-banner__action"
          onClick={() => {
            const now = Date.now();
            for (const s of due) {
              const v = s.updates?.[0]?.version;
              if (s.hub_entry && v !== undefined) remindLater(me, s.hub_entry, v, now);
            }
          }}
        >
          {t("skills.updates.later")}
        </button>
      </div>
      {error ? (
        <p className="skill-update-banner__error" role="alert">
          {error}
        </p>
      ) : null}
      {open ? (
        <div className="skill-update-banner__notes">
          {due.map((s) => (
            <div key={s.name} className="skill-update-banner__skill">
              {/* One skill is already named by the banner's line. */}
              {due.length > 1 ? <strong>{s.name}</strong> : null}
              <SkillUpdates
                skill={s}
                canUpdate={canUpdate && !s.readonly}
                onUpdate={() => void update(s.name)}
                bare
              />
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
