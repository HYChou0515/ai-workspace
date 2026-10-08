import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ComponentProps, useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { FileService } from "../api/fileService";
import { qk } from "../api/queryKeys";
import { invalidateHubInstalls } from "../api/skillHubCache";
import type { ApiClient, ItemSkillState, ToolPref } from "../api/types";
import { skillDir } from "../api/workspaceSkills";
import { useT } from "../lib/i18n";
import { sameShape } from "../lib/sameShape";
import { Icon } from "./Icon";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { filesHere, hubCopy } from "../lib/skillFiles";
import { useContainerWidth } from "../hooks/useContainerWidth";
import { publishAgentDraft } from "../lib/agentDraftBus";
import { ActionMenu, type ActionMenuItem } from "./ActionMenu";
import { ModalShell } from "./ModalShell";
import { SkillHubPicker } from "./SkillHubPicker";

/**
 * The Skills panel (#298 + #380). Lists every skill available to this item —
 * the App's declared shared skills, the profile's package skills, and the ones
 * the user co-created in THIS workspace (`.skill/<name>/SKILL.md`, hidden from the
 * IDE tree). Each row carries a persistent tri-state toggle (Default / On / Off,
 * stored in `attached_skill_prefs`, mirroring the tool picker) and a one-shot
 * "Apply" that loads the skill into the assistant's next turn. Workspace skills
 * additionally download as a folder zip; a folder imports back via the file routes.
 */
export function SkillsModal({
  slug,
  itemId,
  fileService,
  onClose,
  onSaveSkillPrefs,
  appliedSkills = [],
  onToggleApply,
  client = api,
  hubClient,
}: {
  slug: string;
  itemId: string;
  fileService: FileService;
  onClose: () => void;
  /** Persist the tri-state override (`attached_skill_prefs`) — wired to the item. */
  onSaveSkillPrefs?: (prefs: Record<string, boolean>) => void | Promise<void>;
  /** Skills the user has queued to apply this turn (composer-owned, one-shot). */
  appliedSkills?: string[];
  /** Toggle a skill in this turn's apply set. */
  onToggleApply?: (name: string) => void;
  client?: Pick<ApiClient, "getItemSkills" | "refreshItemSkill">;
  /** The skill hub client the picker installs through (tests inject one). */
  hubClient?: ComponentProps<typeof SkillHubPicker>["client"];
}) {
  const t = useT();
  const qc = useQueryClient();
  const skillsQ = useQuery({
    queryKey: qk.itemSkills(slug, itemId),
    queryFn: () => client.getItemSkills(slug, itemId),
  });
  const importRef = useRef<HTMLInputElement | null>(null);
  // What the last refresh left alone. Shown because "we did not touch these" is
  // the only part of the result the user has to act on.
  const [refreshNote, setRefreshNote] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // 「從 skill hub 裝」 (plan D2): a page of this panel, with a way back
  // (plan-skill-hub-ux-redo D9) — it used to be a modal over the modal.
  const [picking, setPicking] = useState(false);
  const [prefs, setPrefs] = useState<Record<string, boolean> | null>(null);
  const [initial, setInitial] = useState<Record<string, boolean> | null>(null);
  const [saving, setSaving] = useState(false);

  // The footer's own width (plan-skill-hub-ui-polish D13): three buttons and
  // a hint share one line, and at phone width the hint folded into three
  // lines beside them. Below FOOTER_HINT_MIN_WIDTH the hint is not drawn — its
  // text lives on as the Import button's title. 0 is "not measured yet"
  // (or a test DOM), never "narrow": an unmeasured footer hides nothing.
  const [footerRef, footerW] = useContainerWidth<HTMLDivElement>();
  const showImportHint = footerW === 0 || footerW >= FOOTER_HINT_MIN_WIDTH;
  // Seed the editable sparse override once the resolved state loads (present
  // on/off entries only — an absent key follows the profile/App default).
  // `initial` keeps that seed so #779 can tell an edited list from an untouched
  // one: this is a long list of tri-states, and re-picking through it is the
  // whole cost of closing by mistake.
  useEffect(() => {
    if (prefs === null && skillsQ.data) {
      const seeded = overrideFromSkills(skillsQ.data);
      setPrefs(seeded);
      setInitial(seeded);
    }
  }, [prefs, skillsQ.data]);

  // sameShape, not JSON.stringify: setState deletes a key for "follow" and
  // re-adds it for on/off, so toggling a skill away and back reorders the object
  // and the modal would claim unsaved work over an identical set.
  const attemptClose = useDirtyClose(initial !== null && !sameShape(prefs, initial), onClose);

  const list = skillsQ.data ?? [];
  const applied = new Set(appliedSkills);

  const stateOf = (name: string): ToolPref =>
    prefs && name in prefs ? (prefs[name] ? "on" : "off") : "follow";

  const setState = (name: string, next: ToolPref) => {
    setPrefs((prev) => {
      const out = { ...(prev ?? {}) };
      if (next === "follow") delete out[name];
      else out[name] = next === "on";
      return out;
    });
  };

  const save = async () => {
    if (prefs === null || saving) return;
    setSaving(true);
    try {
      await onSaveSkillPrefs?.(prefs);
      await qc.invalidateQueries({ queryKey: qk.itemSkills(slug, itemId) });
      onClose();
    } finally {
      setSaving(false);
    }
  };

  const refresh = async (name: string, force: boolean) => {
    setRefreshError(null);
    let res: Awaited<ReturnType<typeof client.refreshItemSkill>>;
    try {
      res = await client.refreshItemSkill(slug, itemId, name, { force });
    } catch (e) {
      // A refusal is the server's sentence for the person (a copy that can
      // only be reset, say); shown, never swallowed.
      setRefreshNote(null);
      setRefreshError(e instanceof Error ? e.message : String(e));
      return;
    }
    const skill = list.find((s) => s.name === name);
    setRefreshNote(
      res.skipped.length > 0
        ? `${t("skills.refreshKept")}: ${res.skipped.join(", ")}`
        : t(
            skill && hubCopy(skill)
              ? "skills.refreshDone.hub"
              : "skills.refreshDone",
          ),
    );
    await qc.invalidateQueries({ queryKey: qk.itemSkills(slug, itemId) });
  };

  // 「發布到 skill hub」: a sentence into the chat box, not a request — the
  // agent's `publish_skill` checks the folder, has it reviewed and reports in
  // the chat (the author: 「上傳必須在item內上傳 這樣才有辦法讓ai審核 而且有問題
  // 馬上可以在對話窗看到」). Offered, not sent, the WUI's idiom: what to say
  // next is still theirs. The panel closes so the box is in front of them —
  // through the SAME exit as ✕ and Escape (#779): a deliberate close that
  // holds unsaved picks asks first (the first version called `onClose`
  // bare and threw them away in silence).
  const publish = (name: string) => {
    publishAgentDraft(itemId, t("skills.publishSentence", { name }));
    attemptClose();
  };

  const installed = async (name: string) => {
    setPicking(false);
    invalidateHubInstalls(qc);
    setRefreshNote(t("skills.fromHub.installed", { name }));
    await qc.invalidateQueries({ queryKey: qk.itemSkills(slug, itemId) });
    await qc.invalidateQueries({ queryKey: qk.files(itemId) });
  };

  const download = async (name: string) => {
    const prefix = skillDir(name);
    const prep = await fileService.prepareDirDownload(prefix);
    const a = document.createElement("a");
    a.href = fileService.dirDownloadUrl(prep.download_id, prefix);
    a.download = prep.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const importFolder = async (files: FileList) => {
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        // The picked folder's name becomes the skill name; `webkitRelativePath`
        // already carries it as the first segment (e.g. `my-skill/SKILL.md`).
        const rel = f.webkitRelativePath || f.name;
        await fileService.writeFile(`.skill/${rel}`, await f.arrayBuffer());
      }
      invalidateHubInstalls(qc);
      await qc.invalidateQueries({ queryKey: qk.itemSkills(slug, itemId) });
      await qc.invalidateQueries({ queryKey: qk.files(itemId) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalShell
      onClose={attemptClose}
      ariaLabel={t("skills.title")}
      data-testid="skills-modal"
      // Each row is two lines with three fixed controls (D9), so the panel
      // no longer has to widen for a copy's six; 640 keeps a long name and a
      // status on the first line.
      width={640}
      maxWidth="92vw"
      panelStyle={{
        padding: 18,
        display: "flex",
        flexDirection: "column",
        gap: 10,
        minHeight: 0,
      }}
    >
        {picking ? (
          <SkillHubPicker
            slug={slug}
            itemId={itemId}
            taken={new Set(list.filter(filesHere).map((s) => s.name))}
            onInstalled={(name) => void installed(name)}
            onBack={() => setPicking(false)}
            client={hubClient}
          />
        ) : (
          <>
        <div className="skills-panel-head">
          <Icon name="sparkle" size={15} />
          <strong>{t("skills.title")}</strong>
          <button
            type="button"
            className="skills-panel-close"
            aria-label={t("skills.close")}
            onClick={attemptClose}
          >
            <Icon name="x" size={14} />
          </button>
        </div>

        <p className="skills-panel-intro">{t("skills.intro")}</p>

        {refreshError && (
          <p className="error" role="alert">
            {refreshError}
          </p>
        )}
        {refreshNote && (
          <p data-testid="skills-refresh-note" className="skills-panel-note" role="status">
            {refreshNote}
          </p>
        )}
        <div className="scrollable skills-panel-list">
          {list.length === 0 ? (
            <p data-testid="skills-empty" className="muted">
              {t("skills.empty")}
            </p>
          ) : (
            list.map((s) => (
              <SkillRow
                key={s.name}
                skill={s}
                state={stateOf(s.name)}
                onSetState={(next) => setState(s.name, next)}
                applied={applied.has(s.name)}
                onToggleApply={() => onToggleApply?.(s.name)}
                // #589: a copy's files are in the workspace even though the row reports
                // the package source it came from, so both cases are downloadable.
                // A readonly skill's copy is the platform's, not the person's:
                // it reads like a skill never copied, with nothing to take away.
                onDownload={
                filesHere(s) && !s.readonly ? () => void download(s.name) : undefined
                }
                // Update only when there is something to bring; reset whenever
                // there is an upstream to bring it FROM — it is the way back
                // from an edit gone wrong, and that need has nothing to do
                // with upstream having moved. Neither on a copy whose upstream
                // is KNOWN to be gone (`unpublished` / `deleted`): the row says
                // so instead, and a press there did nothing and then said
                // "Updated". An absent `upstream` (an older API pod mid-
                // rollout) keeps today's behaviour.
                // A readonly skill updates itself on the next read and is never
                // edited, so neither control has anything to do there.
                onRefresh={
                  s.update_available && !upstreamGone(s) && !s.readonly
                    ? () => void refresh(s.name, false)
                    : undefined
                }
                onReset={
                  s.is_copy && !upstreamGone(s) && !s.readonly
                    ? () => void refresh(s.name, true)
                    : undefined
                }
                // Only a skill whose files are HERE can be published: a
                // hand-written one, or a copy installed from the skill hub
                // (both read `source: workspace`). A package skill's files are
                // the deploy's.
                onPublish={s.source === "workspace" ? () => publish(s.name) : undefined}
              />
            ))
          )}
        </div>

        <div ref={footerRef} className="skills-panel-foot">
          <button
            type="button"
            className="btn"
            data-size="sm"
            data-variant="secondary"
            data-testid="skills-import"
            disabled={busy}
            title={t("skills.importHint")}
            onClick={() => importRef.current?.click()}
          >
            <Icon name="upload" size={12} /> {t("skills.import")}
          </button>
          <button
            type="button"
            className="btn"
            data-size="sm"
            data-variant="secondary"
            data-testid="skills-from-hub"
            disabled={busy}
            onClick={() => setPicking(true)}
          >
            <Icon name="sparkle" size={12} /> {t("skills.fromHub")}
          </button>
          {showImportHint ? (
            <span data-testid="skills-import-hint" className="skills-panel-hint">
              {t("skills.importHint")}
            </span>
          ) : (
            <span style={{ flex: 1 }} />
          )}
          <button
            type="button"
            className="btn"
            data-size="sm"
            data-variant="primary"
            data-testid="skills-save"
            disabled={saving || prefs === null}
            onClick={() => void save()}
          >
            {t("skills.save")}
          </button>
          <input
            ref={(el) => {
              importRef.current = el;
              // `webkitdirectory` isn't in the HTMLInputElement type — set it raw so
              // the picker selects a whole skill folder (SKILL.md + references/scripts).
              if (el) el.setAttribute("webkitdirectory", "");
            }}
            type="file"
            data-testid="skills-import-input"
            style={{ display: "none" }}
            onChange={(e) => {
              const files = e.target.files;
              if (files && files.length) void importFolder(files);
              e.target.value = "";
            }}
          />
        </div>
          </>
        )}
    </ModalShell>
  );
}

/** Where a skill comes from, in words — never the internal `shared` /
 * `profile` / `workspace` (plan-skill-hub-ux-redo D9). */
const SOURCE_WORD = {
  shared: "skills.source.shared",
  profile: "skills.source.profile",
  workspace: "skills.source.workspace",
} as const;

/** One skill (plan-skill-hub-ux-redo D9): two lines — the name with what it
 * is and what needs doing, then the description — and three controls that
 * are always there, in the same place on every row: Apply, the tri-state,
 * and ⋯ for the rest (Material 3's overflow menu). A state that needs
 * something done carries the action beside its words (Polaris / GOV.UK):
 * 「有新版 ・ 更新」. */
function SkillRow({
  skill,
  state,
  onSetState,
  applied,
  onToggleApply,
  onDownload,
  onRefresh,
  onReset,
  onPublish,
}: {
  skill: ItemSkillState;
  state: ToolPref;
  onSetState: (next: ToolPref) => void;
  applied: boolean;
  onToggleApply: () => void;
  onDownload?: () => void;
  /** #589 — only a local COPY of a baked-in skill can be refreshed from
   * upstream; a skill written here has no upstream to refresh from. */
  onRefresh?: () => void;
  /** #589 — restore every shipped file, including ones edited here. Offered on
   * any copy: it is the way back from an edit gone wrong. */
  onReset?: () => void;
  /** Skill hub (D2): offered on a skill whose files are in this workspace. */
  onPublish?: () => void;
}) {
  const t = useT();
  // "The shipped version" is the package's phrase; a hub copy updates to,
  // and resets to, the version on the hub (D4).
  const fromHub = hubCopy(skill);
  const sourceKey = SOURCE_WORD[skill.source as keyof typeof SOURCE_WORD];
  const more: ActionMenuItem[] = [];
  if (onRefresh)
    more.push({
      id: "refresh",
      label: t(fromHub ? "skills.refresh.hub" : "skills.refresh"),
      onSelect: onRefresh,
      testId: `skill-refresh-${skill.name}`,
    });
  if (onReset)
    more.push({
      id: "reset",
      label: t(fromHub ? "skills.reset.hub" : "skills.reset"),
      onSelect: onReset,
      testId: `skill-reset-${skill.name}`,
    });
  if (onDownload)
    more.push({
      id: "download",
      label: t("skills.download"),
      onSelect: onDownload,
      testId: `skill-download-${skill.name}`,
    });
  if (onPublish)
    more.push({
      id: "publish",
      label: t("skills.publish"),
      onSelect: onPublish,
      testId: `skill-publish-${skill.name}`,
    });
  return (
    <div data-testid={`skill-row-${skill.name}`} className="skills-row">
      <div className="skills-row-text">
        <div className="skills-row-line">
          <span className="skills-row-name">{skill.name}</span>
          <span className="skills-row-status">
            <span data-testid={`skill-source-${skill.name}`}>
              {fromHub ? t("skills.source.hub") : sourceKey ? t(sourceKey) : skill.source}
            </span>
            {skill.is_copy && !skill.readonly && !fromHub ? (
              // A copy of a package skill IS editable here, and that is the
              // whole point of copying it.
              <span data-testid={`skill-copy-${skill.name}`}>{t("skills.copy")}</span>
            ) : null}
            {skill.update_available && !upstreamGone(skill) && !skill.readonly ? (
              <span data-testid={`skill-update-${skill.name}`} className="skills-row-todo">
                {t(fromHub ? "skills.updateAvailable.hub" : "skills.updateAvailable")}
                {onRefresh ? (
                  <button
                    type="button"
                    className="skills-row-link"
                    data-testid={`skill-update-go-${skill.name}`}
                    onClick={onRefresh}
                  >
                    {t(fromHub ? "skills.refresh.hub" : "skills.refresh.short")}
                  </button>
                ) : null}
              </span>
            ) : null}
            {upstreamGone(skill) ? (
              // A copy whose skill hub original went away (plan P5): said on
              // the row, so the missing Update reads as explained, not broken.
              // The copy itself keeps working.
              <span data-testid={`skill-upstream-${skill.name}`} className="skills-row-warn">
                {skill.upstream === "unpublished"
                  ? t("skillHub.origin.unpublished")
                  : t("skillHub.origin.deleted")}
              </span>
            ) : null}
          </span>
        </div>
        <div className="skills-row-desc" title={skill.description}>
          {skill.description}
        </div>
      </div>

      <div data-testid={`skill-actions-${skill.name}`} className="skills-row-controls">
        <button
          type="button"
          className="btn"
          data-size="sm"
          data-variant={applied ? "primary" : "secondary"}
          data-testid={`skill-apply-${skill.name}`}
          aria-pressed={applied}
          title={t("skills.applyTip")}
          onClick={onToggleApply}
        >
          {t("skills.apply")}
        </button>
        <div role="group" aria-label={t("skills.state")} className="skills-row-seg">
          {(["follow", "on", "off"] as ToolPref[]).map((opt) => (
            <button
              key={opt}
              type="button"
              data-testid={`skill-${skill.name}-${opt}`}
              aria-pressed={state === opt}
              onClick={() => onSetState(opt)}
            >
              {t(opt === "follow" ? "tools.follow" : opt === "on" ? "tools.on" : "tools.off")}
            </button>
          ))}
        </div>
        {/* The same slot on every row, so the controls line up down the
            list; empty when there is nothing more to do. */}
        <span className="skills-row-more">
          {more.length > 0 ? (
            <ActionMenu
              label={t("skills.more", { name: skill.name })}
              iconOnly
              items={more}
              testId={`skill-more-${skill.name}`}
            />
          ) : null}
        </span>
      </div>
    </div>
  );
}


/** Below this footer width the import hint is not drawn (D13). Measured:
 * at the panel's 640 the footer is 602 px; at a 390 phone it is 304, where
 * the three buttons leave the hint a column a few characters wide. */
const FOOTER_HINT_MIN_WIDTH = 480;
/** Whether the copy's skill hub original is known to be gone (plan P5's two
 * dead states). `undefined` — a server that does not say — is not gone. */
function upstreamGone(s: ItemSkillState): boolean {
  return s.upstream === "unpublished" || s.upstream === "deleted";
}

function overrideFromSkills(skills: ItemSkillState[]): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (const s of skills) {
    if (s.pref === "on") out[s.name] = true;
    else if (s.pref === "off") out[s.name] = false;
  }
  return out;
}


