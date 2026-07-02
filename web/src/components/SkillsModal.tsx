import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { FileService } from "../api/fileService";
import { qk } from "../api/queryKeys";
import type { ApiClient, ItemSkillState, ToolPref } from "../api/types";
import { skillDir } from "../api/workspaceSkills";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { Icon } from "./Icon";

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
  client?: Pick<ApiClient, "getItemSkills">;
}) {
  const t = useT();
  const qc = useQueryClient();
  const skillsQ = useQuery({
    queryKey: qk.itemSkills(slug, itemId),
    queryFn: () => client.getItemSkills(slug, itemId),
  });
  const importRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [prefs, setPrefs] = useState<Record<string, boolean> | null>(null);
  const [saving, setSaving] = useState(false);

  // Seed the editable sparse override once the resolved state loads (present
  // on/off entries only — an absent key follows the profile/App default).
  useEffect(() => {
    if (prefs === null && skillsQ.data) setPrefs(overrideFromSkills(skillsQ.data));
  }, [prefs, skillsQ.data]);

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
      await qc.invalidateQueries({ queryKey: qk.itemSkills(slug, itemId) });
      await qc.invalidateQueries({ queryKey: qk.files(itemId) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="presentation"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 200,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("skills.title")}
        data-testid="skills-modal"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 520,
          maxWidth: "92vw",
          maxHeight: "82vh",
          background: "var(--white)",
          borderRadius: "var(--radius-card)",
          border: "1px solid var(--paper-3)",
          boxShadow: "0 16px 40px rgba(0,0,0,0.22)",
          padding: 18,
          display: "flex",
          flexDirection: "column",
          gap: 10,
          minHeight: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Icon name="sparkle" size={15} />
          <strong style={{ flex: 1 }}>{t("skills.title")}</strong>
          <button
            type="button"
            aria-label={t("skills.close")}
            onClick={onClose}
            style={{ border: "none", background: "transparent", cursor: "pointer" }}
          >
            <Icon name="x" size={14} />
          </button>
        </div>

        <p style={{ margin: 0, fontSize: "var(--text-body-sm)", color: "var(--text-paper-d)" }}>
          {t("skills.intro")}
        </p>

        <div
          style={{ overflowY: "auto", display: "flex", flexDirection: "column", gap: 4, flex: 1 }}
        >
          {list.length === 0 ? (
            <p
              data-testid="skills-empty"
              style={{ fontSize: "var(--text-body-sm)", color: "var(--text-paper-d)" }}
            >
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
                onDownload={s.source === "workspace" ? () => void download(s.name) : undefined}
              />
            ))
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
          <button
            type="button"
            data-testid="skills-import"
            disabled={busy}
            onClick={() => importRef.current?.click()}
            style={pillBtn}
          >
            <Icon name="upload" size={12} /> {t("skills.import")}
          </button>
          <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)", flex: 1 }}>
            {t("skills.importHint")}
          </span>
          <button
            type="button"
            data-testid="skills-save"
            disabled={saving || prefs === null}
            onClick={() => void save()}
            style={{
              ...pillBtn,
              background: "var(--accent)",
              color: "var(--white)",
              borderColor: "var(--accent)",
            }}
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
      </div>
    </div>
  );
}

function SkillRow({
  skill,
  state,
  onSetState,
  applied,
  onToggleApply,
  onDownload,
}: {
  skill: ItemSkillState;
  state: ToolPref;
  onSetState: (next: ToolPref) => void;
  applied: boolean;
  onToggleApply: () => void;
  onDownload?: () => void;
}) {
  const t = useT();
  return (
    <div
      data-testid={`skill-row-${skill.name}`}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 6px",
        borderRadius: "var(--radius-btn)",
        fontSize: pxToRem(13),
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontWeight: 600 }}>{skill.name}</span>
          <span
            data-testid={`skill-source-${skill.name}`}
            style={{
              fontSize: pxToRem(10),
              color: "var(--text-paper-d)",
              border: "1px solid var(--paper-3)",
              borderRadius: 999,
              padding: "0 6px",
            }}
          >
            {skill.source}
          </span>
        </div>
        <div
          style={{
            fontSize: pxToRem(11),
            color: "var(--text-paper-d)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {skill.description}
        </div>
      </div>

      <button
        type="button"
        data-testid={`skill-apply-${skill.name}`}
        aria-pressed={applied}
        title={t("skills.applyTip")}
        onClick={onToggleApply}
        style={{
          ...pillBtn,
          height: 24,
          background: applied ? "var(--accent)" : "var(--white)",
          color: applied ? "var(--white)" : "var(--text-paper)",
          borderColor: applied ? "var(--accent)" : "var(--paper-3)",
        }}
      >
        <Icon name="sparkle" size={11} /> {t("skills.apply")}
      </button>

      {onDownload && (
        <button
          type="button"
          data-testid={`skill-download-${skill.name}`}
          aria-label={`${t("skills.download")} ${skill.name}`}
          onClick={onDownload}
          style={{ ...pillBtn, height: 24 }}
        >
          <Icon name="download" size={12} />
        </button>
      )}

      <div
        role="group"
        style={{
          display: "flex",
          border: "1px solid var(--paper-3)",
          borderRadius: "var(--radius-btn)",
          overflow: "hidden",
        }}
      >
        {(["follow", "on", "off"] as ToolPref[]).map((opt) => (
          <button
            key={opt}
            type="button"
            data-testid={`skill-${skill.name}-${opt}`}
            aria-pressed={state === opt}
            onClick={() => onSetState(opt)}
            style={segBtn(state === opt)}
          >
            {t(opt === "follow" ? "tools.follow" : opt === "on" ? "tools.on" : "tools.off")}
          </button>
        ))}
      </div>
    </div>
  );
}

function overrideFromSkills(skills: ItemSkillState[]): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (const s of skills) {
    if (s.pref === "on") out[s.name] = true;
    else if (s.pref === "off") out[s.name] = false;
  }
  return out;
}

function segBtn(active: boolean): React.CSSProperties {
  return {
    height: 24,
    padding: "0 10px",
    fontSize: pxToRem(12),
    border: "none",
    borderRight: "1px solid var(--paper-3)",
    background: active ? "var(--accent)" : "var(--white)",
    color: active ? "var(--white)" : "var(--text-paper)",
    cursor: "pointer",
  };
}

const pillBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  height: 24,
  padding: "0 8px",
  fontSize: pxToRem(11),
  borderRadius: "var(--radius-btn)",
  border: "1px solid var(--paper-3)",
  background: "var(--white)",
  cursor: "pointer",
  whiteSpace: "nowrap",
};
