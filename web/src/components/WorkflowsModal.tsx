import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import type { FileService } from "../api/fileService";
import { qk } from "../api/queryKeys";
import { TemplateConflictError, workflowTemplatesApi } from "../api/workflowTemplates";
import { workflowApi } from "../api/workflows";
import { WORKFLOWS_DIR } from "../api/workspaceWorkflows";
import { useWorkflowTemplates } from "../hooks/useWorkflowTemplates";
import { useWorkspaceWorkflows } from "../hooks/useWorkspaceWorkflows";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { Icon } from "./Icon";
import { useDialog } from "./Dialog";
import { ModalShell } from "./ModalShell";

/**
 * The Workflows panel (#323) — lists the workflows the user co-created with the agent in
 * THIS workspace (`.workflows/<id>.json`), since the IDE tree hides the dot-folder. A
 * workflow is DATA the platform interprets, not code, so it's safe to run: each row has a
 * **Run** button (the workspace self-serve trigger, P4). The whole `.workflows/` folder
 * downloads as a zip (hand it to the team to bake into the starting profile); a `.json`
 * imports back via the file routes. Creating one is a conversation — the empty state
 * points the user at the agent (the `author-workflow` skill).
 */
export function WorkflowsModal({
  slug,
  itemId,
  fileService,
  onClose,
  onRun,
}: {
  slug: string;
  itemId: string;
  fileService: FileService;
  onClose: () => void;
  /** Called with the new run's chat id after a successful launch, so the parent can
   * focus the run's chat. */
  onRun?: (chatId: string) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const dialog = useDialog();
  const workflows = useWorkspaceWorkflows(slug, itemId);
  const templates = useWorkflowTemplates(slug, itemId);
  const importRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);

  /** #520: pull a shipped template in. A name clash comes back as a 409 rather than
   * quietly overwriting, so we ask before replacing — the copy in the workspace may
   * already carry the user's edits. */
  const copyTemplate = async (id: string, title: string) => {
    setBusy(true);
    try {
      try {
        await workflowTemplatesApi.copy(slug, itemId, id);
      } catch (err) {
        if (!(err instanceof TemplateConflictError)) throw err;
        const choice = await dialog.confirm({
          title: t("templates.heading"),
          body: t("templates.replaceConfirm", { name: title || id }),
          actions: [
            { id: "replace", label: t("templates.replace"), variant: "danger" },
            { id: "cancel", label: t("workflows.close") },
          ],
        });
        if (choice !== "replace") return;
        await workflowTemplatesApi.copy(slug, itemId, id, { overwrite: true });
      }
      await qc.invalidateQueries({ queryKey: qk.workspaceWorkflows(slug, itemId) });
      await qc.invalidateQueries({ queryKey: qk.files(itemId) });
    } finally {
      setBusy(false);
    }
  };

  const run = async (id: string) => {
    setBusy(true);
    try {
      const res = await workflowApi.startRun(slug, itemId, id);
      onRun?.(res.chat_id);
      onClose();
    } finally {
      setBusy(false);
    }
  };

  const downloadAll = async () => {
    const prep = await fileService.prepareDirDownload(WORKFLOWS_DIR);
    const a = document.createElement("a");
    a.href = fileService.dirDownloadUrl(prep.download_id, WORKFLOWS_DIR);
    a.download = prep.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const importFiles = async (files: FileList) => {
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        await fileService.writeFile(`${WORKFLOWS_DIR}/${f.name}`, await f.arrayBuffer());
      }
      await qc.invalidateQueries({ queryKey: qk.workspaceWorkflows(slug, itemId) });
      await qc.invalidateQueries({ queryKey: qk.files(itemId) });
    } finally {
      setBusy(false);
    }
  };

  const list = workflows.data ?? [];

  return (
    <ModalShell
      onClose={onClose}
      ariaLabel={t("workflows.title")}
      data-testid="workflows-modal"
      width={480}
      maxWidth="92vw"
      panelStyle={{
        padding: 18,
        display: "flex",
        flexDirection: "column",
        gap: 10,
        minHeight: 0,
      }}
    >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Icon name="workflow" size={15} />
          <strong style={{ flex: 1 }}>{t("workflows.title")}</strong>
          <button
            type="button"
            aria-label={t("workflows.close")}
            onClick={onClose}
            style={{ border: "none", background: "transparent", cursor: "pointer" }}
          >
            <Icon name="x" size={14} />
          </button>
        </div>

        <p style={{ margin: 0, fontSize: "var(--text-body-sm)", color: "var(--text-paper-d)" }}>
          {t("workflows.intro")}
        </p>

        <div style={{ overflowY: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
          {list.length === 0 ? (
            <p
              data-testid="workflows-empty"
              style={{ fontSize: "var(--text-body-sm)", color: "var(--text-paper-d)" }}
            >
              {t("workflows.empty")}
            </p>
          ) : (
            list.map((w) => (
              <div
                key={w.id}
                data-testid="workflow-row"
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
                  <div style={{ fontWeight: 600, fontSize: "var(--text-body-sm)" }}>
                    {w.title || w.id}
                  </div>
                  <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
                    {t("workflows.steps", { n: w.phases.length })}
                  </div>
                </div>
                <button
                  type="button"
                  data-testid={`workflow-run-${w.id}`}
                  aria-label={`${t("workflows.run")} ${w.title || w.id}`}
                  disabled={busy}
                  onClick={() => void run(w.id)}
                  style={pillBtn}
                >
                  <Icon name="play" size={12} /> {t("workflows.run")}
                </button>
              </div>
            ))
          )}
        </div>

        {(templates.data ?? []).length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
            <strong style={{ fontSize: "var(--text-body-sm)" }}>{t("templates.heading")}</strong>
            <p
              style={{
                margin: 0,
                fontSize: pxToRem(11),
                color: "var(--text-paper-d)",
              }}
            >
              {t("templates.intro")}
            </p>
            {(templates.data ?? []).map((tpl) => (
              <div
                key={tpl.id}
                data-testid="workflow-template-row"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 8px",
                  border: "1px solid var(--paper-3)",
                  borderRadius: "var(--radius-btn)",
                  // #520: an unusable template stays VISIBLE but reads as inert, so the
                  // user learns it exists and what would make it work.
                  opacity: tpl.compatible ? 1 : 0.55,
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: "var(--text-body-sm)" }}>
                    {tpl.title || tpl.id}
                  </div>
                  <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
                    {tpl.compatible ? tpl.description : t("templates.unavailable")}
                  </div>
                </div>
                <button
                  type="button"
                  data-testid={`workflow-template-copy-${tpl.id}`}
                  aria-label={`${t("templates.copy")} ${tpl.title || tpl.id}`}
                  disabled={busy || !tpl.compatible}
                  title={tpl.compatible ? undefined : tpl.problems.join("; ")}
                  onClick={() => void copyTemplate(tpl.id, tpl.title)}
                  style={pillBtn}
                >
                  <Icon name="download" size={12} /> {t("templates.copy")}
                </button>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
          <button
            type="button"
            data-testid="workflows-download"
            disabled={list.length === 0}
            onClick={() => void downloadAll()}
            style={pillBtn}
          >
            <Icon name="download" size={12} /> {t("workflows.download")}
          </button>
          <button
            type="button"
            data-testid="workflows-import"
            disabled={busy}
            onClick={() => importRef.current?.click()}
            style={pillBtn}
          >
            <Icon name="upload" size={12} /> {t("workflows.import")}
          </button>
          <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
            {t("workflows.importHint")}
          </span>
          <input
            ref={importRef}
            type="file"
            accept=".json,application/json"
            multiple
            data-testid="workflows-import-input"
            style={{ display: "none" }}
            onChange={(e) => {
              const files = e.target.files;
              if (files && files.length) void importFiles(files);
              e.target.value = "";
            }}
          />
        </div>
    </ModalShell>
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
