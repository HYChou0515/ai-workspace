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
 * first line — with Expand all; and Update all (NN/g, *Bulk Actions*),
 * confirmed first because it rewrites copies everyone in the item shares.
 * What an update did is said here: the files it left alone because they were
 * edited in this item, or why it was refused.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

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

/** The note's first line, as a heading can carry it. */
function firstLine(note: string): string {
  const line = note.split("\n").find((l) => l.trim()) ?? "";
  return line.replace(/^\s*(?:[-*+]|\d+\.)\s+/, "").replace(/[*_`]/g, "").trim();
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
  const { id: me } = useCurrentUserState();
  const due = useDueSkillUpdates(slug, itemId, client);
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);

  const updateOne = async (name: string): Promise<Outcome> => {
    try {
      const res = await client.refreshItemSkill(slug, itemId, name, { force: false });
      return res.skipped.length > 0 ? { name, kept: res.skipped } : { name };
    } catch (e) {
      return { name, error: e instanceof Error ? e.message : String(e) };
    }
  };
  const update = async (names: string[]) => {
    setOutcomes([]);
    const done: Outcome[] = [];
    for (const name of names) done.push(await updateOne(name));
    setOutcomes(done.filter((o) => o.kept || o.error));
    await qc.invalidateQueries({ queryKey: qk.itemSkills(slug, itemId) });
  };

  const notices =
    outcomes.length > 0 ? (
      <>
        {outcomes.some((o) => o.kept) ? (
          <ul className="skill-update-banner__status" role="status">
            {outcomes
              .filter((o) => o.kept)
              .map((o) => (
                <li key={o.name}>{`${o.name}: ${t("skills.refreshKept")}: ${o.kept?.join(", ")}`}</li>
              ))}
          </ul>
        ) : null}
        {outcomes.some((o) => o.error) ? (
          <ul className="skill-update-banner__error" role="alert">
            {outcomes
              .filter((o) => o.error)
              .map((o) => (
                <li key={o.name}>{`${o.name}: ${o.error}`}</li>
              ))}
          </ul>
        ) : null}
      </>
    ) : null;

  if (due.length === 0) {
    // Everything is answered — but what the last update did is still news.
    return notices ? (
      <section className="skill-update-banner" aria-label={t("skills.banner.label")}>
        {notices}
      </section>
    ) : null;
  }

  const latestOf = (s: ItemSkillState) => s.updates?.[0];
  const sep = t("skills.banner.sep");
  const names = due.slice(0, 2).map((s) => s.name).join(sep) + (due.length > 2 ? t("skills.banner.andMore") : "");
  const only = due.length === 1 ? latestOf(due[0]) : undefined;
  const message =
    due.length > 1
      ? t("skills.banner.many", { n: due.length, names })
      : only?.kind === "rollback" && only.back_to !== null
        ? t("skills.banner.rolledBack", { name: due[0].name, back: only.back_to })
        : t("skills.banner.one", { name: due[0].name, version: only?.version ?? 0 });
  const editable = (s: ItemSkillState) => canUpdate && !s.readonly;

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
      title: t("skills.banner.confirmTitle", { n: targets.length }),
      body: t("skills.banner.confirmBody", { names: targets.join(sep) }),
      // The safe answer first: a stray Enter never rewrites every copy.
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
      <div className="skill-update-banner__line">
        <span className="skill-update-banner__text">{message}</span>
        <button type="button" className="skill-update-banner__action" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {t("skills.updates.show")}
        </button>
        <button type="button" className="skill-update-banner__action" onClick={later}>
          {t("skills.updates.later")}
        </button>
      </div>
      {notices}
      {open && due.length === 1 ? (
        <div className="skill-update-banner__notes">
          <SkillUpdates skill={due[0]} canUpdate={editable(due[0])} onUpdate={() => void update([due[0].name])} bare />
        </div>
      ) : null}
      {open && due.length > 1 ? (
        <div className="skill-update-banner__notes">
          <div className="skill-update-banner__bulk">
            <button
              type="button"
              className="skill-update-banner__action"
              onClick={() =>
                setExpanded(expanded.size === due.length ? new Set() : new Set(due.map((s) => s.name)))
              }
            >
              {expanded.size === due.length ? t("skills.banner.collapseAll") : t("skills.banner.expandAll")}
            </button>
            {due.some(editable) ? (
              <button type="button" className="skill-update-banner__action" onClick={() => void updateAll()}>
                {t("skills.banner.updateAll")}
              </button>
            ) : null}
          </div>
          <ul className="skill-update-banner__list">
            {due.map((s) => {
              const u = latestOf(s);
              const isOpen = expanded.has(s.name);
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
                      onClick={() => toggle(s.name)}
                    >
                      <span aria-hidden>{isOpen ? "▾" : "▸"}</span>
                      <strong>{s.name}</strong>
                      <span>{`v${u?.version ?? ""}`}</span>
                      <span className="skill-update-banner__gist">{gist}</span>
                    </button>
                    {editable(s) ? (
                      <button type="button" className="skill-update-banner__action" onClick={() => void update([s.name])}>
                        {t("skills.updates.update")}
                      </button>
                    ) : null}
                  </div>
                  {isOpen ? (
                    <SkillUpdates skill={s} canUpdate={false} onUpdate={() => undefined} bare />
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
