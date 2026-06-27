import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import type { FileService } from "../api/fileService";
import { qk } from "../api/queryKeys";
import { skillDir } from "../api/workspaceSkills";
import { useWorkspaceSkills } from "../hooks/useWorkspaceSkills";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { Icon } from "./Icon";

/**
 * The Skills panel (#298) — lists the skills the user co-created with the agent in
 * THIS workspace (`.skill/<name>/SKILL.md`), since the IDE tree hides the dot-folder.
 * Each skill downloads as a folder zip (reuse it elsewhere, or hand it to the team to
 * bake into the starting profile); a skill folder imports back via the file routes.
 * Creating a skill is a conversation — the empty state points the user at the agent.
 */
export function SkillsModal({
  slug,
  itemId,
  fileService,
  onClose,
}: {
  slug: string;
  itemId: string;
  fileService: FileService;
  onClose: () => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const skills = useWorkspaceSkills(slug, itemId);
  const importRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);

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
        // already carries it as the first segment (e.g. `my-skill/SKILL.md`), so
        // the skill lands at `.skill/my-skill/…`.
        const rel = f.webkitRelativePath || f.name;
        await fileService.writeFile(`.skill/${rel}`, await f.arrayBuffer());
      }
      await qc.invalidateQueries({ queryKey: qk.workspaceSkills(slug, itemId) });
      await qc.invalidateQueries({ queryKey: qk.files(itemId) });
    } finally {
      setBusy(false);
    }
  };

  const list = skills.data ?? [];

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
          width: 480,
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

        <div style={{ overflowY: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
          {list.length === 0 ? (
            <p
              data-testid="skills-empty"
              style={{ fontSize: "var(--text-body-sm)", color: "var(--text-paper-d)" }}
            >
              {t("skills.empty")}
            </p>
          ) : (
            list.map((s) => (
              <div
                key={s.name}
                data-testid="skill-row"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 8px",
                  border: "1px solid var(--paper-3)",
                  borderRadius: "var(--radius-btn)",
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: "var(--text-body-sm)" }}>{s.name}</div>
                  <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
                    {s.description}
                  </div>
                </div>
                <button
                  type="button"
                  data-testid={`skill-download-${s.name}`}
                  aria-label={`${t("skills.download")} ${s.name}`}
                  onClick={() => void download(s.name)}
                  style={pillBtn}
                >
                  <Icon name="download" size={12} /> {t("skills.download")}
                </button>
              </div>
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
          <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
            {t("skills.importHint")}
          </span>
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
