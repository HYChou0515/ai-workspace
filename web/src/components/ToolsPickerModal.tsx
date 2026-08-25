import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { ApiClient, ExternalToolState, ItemToolState } from "../api/types";
import { useT } from "../lib/i18n";
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
 * Third-party tools (#674/#724) render below, read-only. They have no
 * per-item switch, so what they owe the reader is an account instead: which
 * release is running, who published it, and whether it came from the host's
 * cached copy. A declared one that could not be resolved is listed with its
 * reason rather than dropped — an absence reads as a configuration the reader
 * imagined (#480).
 */
export function ToolsPickerModal({
  slug,
  itemId,
  onSave,
  onClose,
  client = api,
}: {
  slug: string;
  itemId: string;
  onSave: (prefs: Record<string, boolean>) => void | Promise<void>;
  onClose: () => void;
  client?: Pick<ApiClient, "getItemTools">;
}) {
  const t = useT();
  const qc = useQueryClient();
  const toolsQ = useQuery({
    queryKey: qk.itemTools(slug, itemId),
    queryFn: () => client.getItemTools(slug, itemId),
  });

  const [prefs, setPrefs] = useState<Record<string, boolean> | null>(null);
  const [initial, setInitial] = useState<Record<string, boolean> | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);

  // Seed the editable override once the resolved state has loaded.
  useEffect(() => {
    if (prefs === null && toolsQ.data) {
      const seeded = overrideFromTools(toolsQ.data.tools);
      setPrefs(seeded);
      setInitial(seeded);
    }
  }, [prefs, toolsQ.data]);

  const ready = prefs !== null && initial !== null && toolsQ.data !== undefined;
  const external = toolsQ.data?.external ?? [];
  const dirty = ready && !sameOverride(prefs!, initial!);

  const attemptClose = () => {
    if (dirty) setConfirming(true);
    else onClose();
  };

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
          <ToolsChecklist tools={toolsQ.data!.tools} prefs={prefs!} onChange={setPrefs} />
        )}

        {ready && external.length > 0 ? <ExternalTools rows={external} /> : null}

        {confirming ? (
          <div
            data-testid="tools-discard-confirm"
            style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "flex-end" }}
          >
            <span style={{ flex: 1, fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>{t("tools.discard")}</span>
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              data-testid="tools-discard-no"
              onClick={() => setConfirming(false)}
            >
              {t("tools.cancel")}
            </button>
            <button
              type="button"
              className="btn"
              data-variant="danger"
              data-size="sm"
              data-testid="tools-discard-yes"
              onClick={onClose}
            >
              {t("tools.resetVisible")}
            </button>
          </div>
        ) : (
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
        )}
    </ModalShell>
  );
}

/** The read-only third-party section (#724). Not part of `ToolsChecklist`:
 * every row there is a control, and one that looked the same but could not be
 * pressed would be the worse lie. */
function ExternalTools({ rows }: { rows: ExternalToolState[] }) {
  const t = useT();
  return (
    <div data-testid="external-tools" style={{ borderTop: "1px solid var(--paper-3)", paddingTop: 10 }}>
      <strong style={{ fontSize: pxToRem(12) }}>{t("tools.external.title")}</strong>
      <p style={{ margin: "4px 0 8px", fontSize: pxToRem(11), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
        {t("tools.external.desc")}
      </p>
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 8 }}>
        {rows.map((row) => (
          <li key={row.key} data-testid={`external-tool-${row.key}`} style={{ fontSize: pxToRem(12) }}>
            <span style={{ fontWeight: 600 }}>{row.key}</span>
            {row.version ? (
              <span style={{ marginLeft: 6, color: "var(--text-paper-d)" }}>{row.version}</span>
            ) : null}
            <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
              {row.unavailable ? (
                <span style={{ color: "var(--err)" }}>
                  {t("tools.external.unavailable", { reason: row.unavailable })}
                </span>
              ) : (
                <>
                  <span>
                    {row.author
                      ? t("tools.external.by", { author: row.author })
                      : t("tools.external.noAuthor")}
                  </span>
                  {row.stale ? (
                    <span data-testid={`external-tool-${row.key}-stale`} style={{ marginLeft: 6 }}>
                      · {t("tools.external.stale")}
                    </span>
                  ) : null}
                </>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
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

function sameOverride(a: Record<string, boolean>, b: Record<string, boolean>): boolean {
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  return ka.every((k) => k in b && a[k] === b[k]);
}
