/**
 * The skill-update reminder ON the item, under its title bar — seen on opening
 * it, not three clicks into the Skills panel (the user, on the first build of
 * #1004: 「他要打開面板才看得到 -> 不會有人看到」).
 *
 * Material 3 Banners: below the top bar; a message and two text buttons; the
 * dismissing action addresses the message ("Remind me later"), never a lone ×;
 * it stays until acted on. NN/g *Indicators, Validations, and Notifications*:
 * a non-urgent message the person should act on is a non-modal notification.
 *
 * However many skills are due it is ONE message, naming the first two (NN/g,
 * *Five Mistakes in Designing Mobile Push Notifications*: more than five
 * pending ones are merged into one). Opened, one skill shows its notes; several
 * are an accordion (NN/g, *Accordions on Desktop*): every row collapsed, its
 * heading carrying what decides whether to open it — name, version, the note's
 * first line — with Expand all; and Update all (NN/g video *Bulk Actions: 3
 * Design Guidelines*: clear feedback), confirmed first because it rewrites
 * copies everyone in the item shares (the user's call). While it runs the
 * controls are off and it says how far it got; afterwards it says what was
 * updated, which files were left alone because they were edited in this item,
 * and what was refused — until the next action or 關閉.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { ApiClient, ItemSkillState } from "../api/types";
import { useCurrentUserState } from "../hooks/useCurrentUser";
import { useDueSkillUpdates } from "../hooks/useSkillUpdateMark";
import { useT } from "../lib/i18n";
import { remindLater } from "../lib/skillUpdateReminders";
import { useDialog } from "./Dialog";
import { SkillUpdates } from "./SkillUpdates";

type Outcome = { name: string; kept?: string[]; error?: string };

/** The note's first line as a heading can carry it: its words, without the
 * markdown around them (a heading's #, a list marker, a quote's >, emphasis,
 * code ticks, a link's URL) — but an identifier's underscores kept. */
export function firstLine(note: string): string {
  const line = note.split("\n").find((l) => l.trim()) ?? "";
  return line
    .replace(/^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+\.\s+)+/, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/(^|\s)\*([^*]+)\*(?=\s|$|[.,;:!?])/g, "$1$2")
    .trim();
}

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
  const dialog = useDialog();
  const rowId = useId();
  const { id: me } = useCurrentUserState();
  const due = useDueSkillUpdates(slug, itemId, client);
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [busy, setBusy] = useState<{ done: number; n: number } | null>(null);
  const sep = t("skills.banner.sep");

  const update = async (names: string[]) => {
    if (busy || names.length === 0) return;
    setOutcomes([]);
    setBusy({ done: 0, n: names.length });
    const done: Outcome[] = [];
    for (const name of names) {
      try {
        const res = await client.refreshItemSkill(slug, itemId, name, { force: false });
        done.push(res.skipped.length > 0 ? { name, kept: res.skipped } : { name });
      } catch (e) {
        done.push({ name, error: e instanceof Error ? e.message : String(e) });
      }
      setBusy({ done: done.length, n: names.length });
    }
    setOutcomes(done);
    setBusy(null);
    await qc.invalidateQueries({ queryKey: qk.itemSkills(slug, itemId) });
  };

  const updated = outcomes.filter((o) => !o.error).map((o) => o.name);
  const kept = outcomes.filter((o) => o.kept);
  const failed = outcomes.filter((o) => o.error);
  // Mounted with the banner and kept mounted, so a reader is told when
  // something is put INTO them (a region born with its text is often missed).
  const live = (
    <>
      <div className="skill-update-banner__status" role="status">
        {busy ? <p>{t("skills.banner.updating", busy)}</p> : null}
        {updated.length > 0 ? <p>{t("skills.banner.updated", { names: updated.join(sep) })}</p> : null}
        {kept.length > 0 ? (
          <ul>
            {kept.map((o) => (
              <li key={o.name}>
                {t("skills.banner.kept", { name: o.name, label: t("skills.refreshKept"), files: o.kept?.join(sep) ?? "" })}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="skill-update-banner__error" role="alert">
        {failed.length > 0 ? (
          <ul>
            {failed.map((o) => (
              <li key={o.name}>{t("skills.banner.failedLine", { name: o.name, error: o.error ?? "" })}</li>
            ))}
          </ul>
        ) : null}
      </div>
      {outcomes.length > 0 && !busy ? (
        <button type="button" className="skill-update-banner__action" onClick={() => setOutcomes([])}>
          {t("skills.banner.dismiss")}
        </button>
      ) : null}
    </>
  );

  // Everything answered and nothing left to say: no banner. Otherwise ONE
  // section, so the live regions stay the same nodes when the last due skill
  // goes and only what the last update did is left — and can be closed.
  if (due.length === 0 && outcomes.length === 0 && !busy) return null;

  const latestOf = (s: ItemSkillState) => s.updates?.[0];
  const names = due.slice(0, 2).map((s) => s.name).join(sep) + (due.length > 2 ? t("skills.banner.andMore") : "");
  const only = due.length === 1 ? latestOf(due[0]) : undefined;
  const message =
    due.length > 1
      ? t("skills.banner.many", { n: due.length, names })
      : due.length === 0
        ? ""
        : only?.kind === "rollback" && only.back_to !== null
          ? t("skills.banner.rolledBack", { name: due[0].name, back: only.back_to })
          : t("skills.banner.one", { name: due[0].name, version: only?.version ?? 0 });
  const editable = (s: ItemSkillState) => canUpdate && !s.readonly;
  // Judged on the rows ON SCREEN: a name left over from a skill that was
  // updated must not make "all open" false, or a new row "all open" true.
  const allOpen = due.every((s) => expanded.has(s.name));
  const off = busy !== null;

  const later = () => {
    setOutcomes([]);
    const now = Date.now();
    for (const s of due) {
      const v = latestOf(s)?.version;
      if (s.hub_entry && v !== undefined) remindLater(me, s.hub_entry, v, now);
    }
  };

  const updateAll = async () => {
    const targets = due.filter(editable).map((s) => s.name);
    const choice = await dialog.confirm({
      title:
        targets.length === 1
          ? t("skills.banner.confirmTitleOne", { name: targets[0] })
          : t("skills.banner.confirmTitle", { n: targets.length }),
      body: t("skills.banner.confirmBody", { names: targets.join(sep) }),
      // Cancel first: from the dialog, the first Tab lands on the answer that
      // changes nothing. (Escape and the backdrop answer null — nothing.)
      actions: [
        { id: "cancel", label: t("skills.banner.cancel") },
        { id: "update", label: t("skills.banner.updateAll"), variant: "primary" },
      ],
    });
    if (choice === "update") await update(targets);
  };

  const toggle = (name: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  return (
    <section className="skill-update-banner" aria-label={t("skills.banner.label")}>
      {due.length > 0 ? (
      <div className="skill-update-banner__line">
        <span className="skill-update-banner__text">{message}</span>
        <button
          type="button"
          className="skill-update-banner__action"
          aria-expanded={open}
          disabled={off}
          onClick={() => setOpen((v) => !v)}
        >
          {t("skills.updates.show")}
        </button>
        <button type="button" className="skill-update-banner__action" disabled={off} onClick={later}>
          {t("skills.updates.later")}
        </button>
      </div>
      ) : null}
      {live}
      {open && due.length === 1 ? (
        <div className="skill-update-banner__notes">
          <SkillUpdates skill={due[0]} canUpdate={editable(due[0]) && !off} onUpdate={() => void update([due[0].name])} bare />
        </div>
      ) : null}
      {open && due.length > 1 ? (
        <div className="skill-update-banner__notes">
          <div className="skill-update-banner__bulk">
            <button
              type="button"
              className="skill-update-banner__action"
              disabled={off}
              onClick={() => setExpanded(allOpen ? new Set() : new Set(due.map((s) => s.name)))}
            >
              {allOpen ? t("skills.banner.collapseAll") : t("skills.banner.expandAll")}
            </button>
            {due.some(editable) ? (
              <button type="button" className="skill-update-banner__action" disabled={off} onClick={() => void updateAll()}>
                {t("skills.banner.updateAll")}
              </button>
            ) : null}
          </div>
          <ul className="skill-update-banner__list">
            {due.map((s, i) => {
              const u = latestOf(s);
              const isOpen = expanded.has(s.name);
              const bodyId = `${rowId}-${i}`;
              const gist =
                u?.kind === "rollback" && u.back_to !== null
                  ? t("skills.updates.rolledBackBadge", { back: u.back_to })
                  : firstLine(u?.release_note ?? "") || t("skills.updates.noNote");
              return (
                <li key={s.name} className="skill-update-banner__row">
                  <div className="skill-update-banner__head">
                    <button
                      type="button"
                      className="skill-update-banner__toggle"
                      aria-expanded={isOpen}
                      aria-controls={bodyId}
                      disabled={off}
                      onClick={() => toggle(s.name)}
                    >
                      <span aria-hidden>{isOpen ? "▾" : "▸"}</span>
                      <strong>{s.name}</strong>
                      <span>{`v${u?.version ?? ""}`}</span>
                      <span className="skill-update-banner__gist">{gist}</span>
                    </button>
                    {editable(s) ? (
                      <button
                        type="button"
                        className="skill-update-banner__action"
                        disabled={off}
                        onClick={() => void update([s.name])}
                      >
                        {t("skills.updates.update")}
                      </button>
                    ) : null}
                  </div>
                  {isOpen ? (
                    <div id={bodyId}>
                      <SkillUpdates skill={s} canUpdate={false} onUpdate={() => undefined} bare />
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
