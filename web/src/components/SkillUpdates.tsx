/**
 * What a skill hub copy behind its entry has missed, under its row in the
 * Skills panel (docs/plan-skill-hub-release-notes D3–D5): every version after
 * the copy's own, newest first, each with what its author wrote — the newest
 * open, the others one click away — and the choice. Update brings the LATEST
 * through the existing sync (D5, D6), offered only to someone who may edit the
 * item (D4); Later hides the reminder for 7 days; Skip, from the second
 * reminder on, silences this version. Later / Skip are this person's, kept in
 * this browser (`skillUpdateReminders`), and judged only once the person is
 * known (#997).
 *
 * NN/g: *Visibility of system status* (the row says it in words, not an icon),
 * *User control and freedom* (Later / Skip), *Recognition rather than recall*
 * (the notes beside the button that acts on them).
 */
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";

import type { ItemSkillState } from "../api/types";
import { useCurrentUserState } from "../hooks/useCurrentUser";
import { useT } from "../lib/i18n";
import { onRemindersChanged, reminderOf, remindLater, skipVersion } from "../lib/skillUpdateReminders";

const when = (iso: string) => {
  const d = new Date(iso);
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`;
};

/** The newest version a behind copy could take, or `null`. */
export function latestUpdate(skill: ItemSkillState) {
  return skill.updates && skill.updates.length > 0 ? skill.updates[0] : null;
}

export function SkillUpdates({
  skill,
  canUpdate,
  onUpdate,
  bare = false,
}: {
  skill: ItemSkillState;
  /** May this viewer change the item's copy (`edit_content`, D4)? */
  canUpdate: boolean;
  onUpdate: () => void;
  /** The notes alone, already open, without the row's own line or its Later —
   * inside the banner, which says both already (plan D7). */
  bare?: boolean;
}) {
  const t = useT();
  const { id: me, ready } = useCurrentUserState();
  const [open, setOpen] = useState(bare);
  // A Later / Skip changes what this person is shown: re-read on it — this
  // row's own, or one given in another tab or by another row.
  const [, setAnswered] = useState(0);
  useEffect(() => onRemindersChanged(() => setAnswered((n) => n + 1)), []);
  const latest = latestUpdate(skill);
  if (!latest || !skill.hub_entry) return null;
  const entry = skill.hub_entry;
  const reminder = ready ? reminderOf(me, entry, latest.version, Date.now()) : null;
  const title = t("skills.updates.title", { name: skill.name });

  return (
    <div className="skills-row-updates" data-testid={`skill-updates-${skill.name}`}>
      {bare ? null : (
      <div className="skills-row-line">
        <span className="skills-row-todo">
          {latest.kind === "rollback" && latest.back_to !== null
            ? t("skills.updates.rolledBackBadge", { back: latest.back_to })
            : t("skills.updates.badge", { version: latest.version })}
        </span>
        {reminder?.skipped ? (
          <span className="muted small">{t("skills.updates.skipped")}</span>
        ) : reminder && !reminder.due ? (
          <span className="muted small">{t("skills.updates.snoozed")}</span>
        ) : null}
        <button
          type="button"
          className="skills-row-link"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {t("skills.updates.show")}
        </button>
      </div>
      )}
      {open ? (
        <section className="skills-updates" aria-label={title}>
          {skill.updates?.map((u, i) => (
            <details key={u.version} open={i === 0} className="skills-update">
              <summary>
                <h4>
                  {u.kind === "rollback" && u.back_to !== null
                    ? t("skills.updates.rolledBack", { version: u.version, back: u.back_to })
                    : t("skills.updates.version", { version: u.version, date: when(u.at) })}
                </h4>
              </summary>
              {u.release_note ? (
                <div className="markdown">
                  <ReactMarkdown>{u.release_note}</ReactMarkdown>
                </div>
              ) : (
                <p className="muted small">
                  {t("skills.updates.noNote")}{" "}
                  <Link to={`/skill-hub/${encodeURIComponent(entry)}?tab=history`}>
                    {t("skills.updates.compare")}
                  </Link>
                </p>
              )}
            </details>
          ))}
          <div className="skills-updates-actions">
            {canUpdate ? (
              <button type="button" className="btn" data-size="sm" data-variant="primary" onClick={onUpdate}>
                {t("skills.updates.update")}
              </button>
            ) : null}
            {reminder?.due && !bare ? (
              <button
                type="button"
                className="btn"
                data-size="sm"
                data-variant="secondary"
                onClick={() => {
                  remindLater(me, entry, latest.version, Date.now());
                  setAnswered((n) => n + 1);
                }}
              >
                {t("skills.updates.later")}
              </button>
            ) : null}
            {reminder?.due && reminder.canSkip ? (
              <button
                type="button"
                className="btn"
                data-size="sm"
                data-variant="secondary"
                onClick={() => {
                  skipVersion(me, entry, latest.version);
                  setAnswered((n) => n + 1);
                }}
              >
                {t("skills.updates.skip")}
              </button>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}
