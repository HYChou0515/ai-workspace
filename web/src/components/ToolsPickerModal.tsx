import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api";
import { type MyResourcesApi, myResourcesApi } from "../api/myResources";
import { qk } from "../api/queryKeys";
import type { ApiClient, ItemToolState } from "../api/types";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { useT } from "../lib/i18n";
import { sameShape } from "../lib/sameShape";
import { pxToRem } from "../lib/pxToRem";
import { ModalShell } from "./ModalShell";
import { ToolsChecklist } from "./ToolsChecklist";

/**
 * The per-item tool picker (#322): a modal over a WorkItem's
 * `attached_tool_prefs` tri-state override. It reads the server-resolved tool
 * state (`GET /a/{slug}/items/{id}/tools` — label, profile default, current
 * override, effective), seeds the editable override from it, and writes the
 * sparse `Record<key, boolean>` back via `onSave` (the parent's read-modify-PUT).
 *
 * The override ceiling is the App's `tools`, so the picker offers every App tool
 * (a force-On can re-add one the profile narrowed away). Persistence is an
 * explicit Save — open reads fresh, Save overwrites the whole override map and
 * invalidates the picker read so reopening reflects it.
 *
 * Third-party tools (#674/#724) are rows here like any other — their
 * `external_tools` key is an `app.json` `tools[]` entry, so they already have a
 * switch. What they carry extra (release, author, cached-copy) rides on the row
 * itself; a separate section listed the same tool twice.
 *
 * plan-tool-running-version: a live sandbox keeps the bundles it was created
 * with, so a row's release can differ from what runs. Such a row says which
 * release runs; the modal says closing the sandbox is what updates it (D1:
 * told, never forced) and, for whoever the close route itself would let close
 * it (D9), offers that close in one button.
 */
export function ToolsPickerModal({
  slug,
  itemId,
  onSave,
  onClose,
  client = api,
  closeClient = myResourcesApi,
}: {
  slug: string;
  itemId: string;
  onSave: (prefs: Record<string, boolean>) => void | Promise<void>;
  onClose: () => void;
  client?: Pick<ApiClient, "getItemTools">;
  closeClient?: Pick<MyResourcesApi, "closeEnvironment">;
}) {
  const t = useT();
  const qc = useQueryClient();
  const toolsQ = useQuery({
    queryKey: qk.itemTools(slug, itemId),
    queryFn: () => client.getItemTools(slug, itemId),
  });

  // The existing close (the resources page's), then read the picker again:
  // with no live sandbox the rows describe the release the next one mounts.
  const closeSandbox = useMutation({
    mutationFn: () => closeClient.closeEnvironment(itemId),
    // Everything else that shows this sandbox reads it again too, as the
    // other close doors do — or the resources gauge keeps a sandbox that is gone.
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.itemTools(slug, itemId) }),
        qc.invalidateQueries({ queryKey: qk.itemEnvironment(slug, itemId) }),
        qc.invalidateQueries({ queryKey: qk.myBudget }),
        qc.invalidateQueries({ queryKey: qk.myResources }),
      ]),
    // Reported here, beside the button: without the opt-out the query client
    // also raises the app-wide write-failure toast — two messages for one press.
    meta: { silentError: true },
  });

  const [prefs, setPrefs] = useState<Record<string, boolean> | null>(null);
  const [initial, setInitial] = useState<Record<string, boolean> | null>(null);
  const [saving, setSaving] = useState(false);

  // Seed the editable override once the resolved state has loaded.
  useEffect(() => {
    if (prefs === null && toolsQ.data) {
      const seeded = overrideFromTools(toolsQ.data.tools);
      setPrefs(seeded);
      setInitial(seeded);
    }
  }, [prefs, toolsQ.data]);

  const ready = prefs !== null && initial !== null && toolsQ.data !== undefined;
  const dirty = ready && !sameShape(prefs, initial);

  const attemptClose = useDirtyClose(dirty, onClose);

  const save = async () => {
    if (!ready || !dirty || saving) return;
    setSaving(true);
    try {
      await onSave(prefs!);
      await qc.invalidateQueries({ queryKey: qk.itemTools(slug, itemId) });
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <ModalShell
      onClose={attemptClose}
      ariaLabel={t("tools.title")}
      data-testid="tools-modal"
      width={480}
      maxWidth="92vw"
      panelStyle={{ padding: 18, display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}
    >
      <strong style={{ fontSize: pxToRem(14) }}>{t("tools.title")}</strong>
        <p style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
          {t("tools.desc")}
        </p>

        {!ready ? (
          <div style={{ flex: 1, minHeight: 0 }}>
            {toolsQ.isError ? (
              <p style={{ fontSize: pxToRem(12), color: "var(--err)" }}>{t("tools.none")}</p>
            ) : (
              <p style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>{t("tools.loading")}</p>
            )}
          </div>
        ) : (
          <>
            {toolsQ.data!.updateNeedsClose ? (
              <div
                role="status"
                data-testid="tools-update-note"
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  alignItems: "center",
                  gap: 8,
                  fontSize: pxToRem(12),
                  color: "var(--text-paper-d)",
                  lineHeight: 1.5,
                }}
              >
                <span style={{ flex: "1 1 200px", minWidth: 0 }}>
                  {toolsQ.data!.canClose ? t("tools.update.note") : t("tools.update.noteOthers")}
                </span>
                {toolsQ.data!.canClose ? (
                  <button
                    type="button"
                    className="btn"
                    data-variant="secondary"
                    data-size="sm"
                    data-testid="tools-update-close"
                    disabled={closeSandbox.isPending}
                    onClick={() => closeSandbox.mutate()}
                  >
                    {t("tools.update.close")}
                  </button>
                ) : null}
                {closeSandbox.isError ? (
                  <span data-testid="tools-update-failed" style={{ flexBasis: "100%", color: "var(--err)" }}>
                    {t("tools.update.failed")}
                  </span>
                ) : null}
              </div>
            ) : null}
            <ToolsChecklist tools={toolsQ.data!.tools} prefs={prefs!} onChange={setPrefs} />
          </>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 2 }}>
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            data-testid="tools-cancel"
            onClick={attemptClose}
          >
            {t("tools.cancel")}
          </button>
          <button
            type="button"
            className="btn"
            data-variant="primary"
            data-size="sm"
            data-testid="tools-save"
            onClick={save}
            disabled={!ready || !dirty || saving}
          >
            {t("tools.save")}
          </button>
        </div>
    </ModalShell>
  );
}

/** Reconstruct the sparse override (`{key: true|false}`, follow keys omitted)
 * from the server-resolved per-tool state. */
function overrideFromTools(tools: ItemToolState[]): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (const tool of tools) {
    if (tool.pref === "on") out[tool.key] = true;
    else if (tool.pref === "off") out[tool.key] = false;
  }
  return out;
}


