import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { ApiClient, ItemToolState } from "../api/types";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
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
      const seeded = overrideFromTools(toolsQ.data);
      setPrefs(seeded);
      setInitial(seeded);
    }
  }, [prefs, toolsQ.data]);

  const ready = prefs !== null && initial !== null && toolsQ.data !== undefined;
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
    <div
      role="presentation"
      onClick={attemptClose}
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
        aria-label={t("tools.title")}
        data-testid="tools-modal"
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
        <strong style={{ fontSize: pxToRem(14) }}>{t("tools.title")}</strong>
        <p style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
          {t("tools.desc")}
        </p>

        {!ready ? (
          <div style={{ flex: 1, minHeight: 0 }}>
            {toolsQ.isError ? (
              <p style={{ fontSize: pxToRem(12), color: "var(--danger, #b4413c)" }}>{t("tools.none")}</p>
            ) : (
              <p style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>{t("tools.loading")}</p>
            )}
          </div>
        ) : (
          <ToolsChecklist tools={toolsQ.data!} prefs={prefs!} onChange={setPrefs} />
        )}

        {confirming ? (
          <div
            data-testid="tools-discard-confirm"
            style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "flex-end" }}
          >
            <span style={{ flex: 1, fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>{t("tools.discard")}</span>
            <button type="button" data-testid="tools-discard-no" onClick={() => setConfirming(false)} style={btn()}>
              {t("tools.cancel")}
            </button>
            <button type="button" data-testid="tools-discard-yes" onClick={onClose} style={btn("danger")}>
              {t("tools.resetVisible")}
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 2 }}>
            <button type="button" data-testid="tools-cancel" onClick={attemptClose} style={btn()}>
              {t("tools.cancel")}
            </button>
            <button
              type="button"
              data-testid="tools-save"
              onClick={save}
              disabled={!ready || !dirty || saving}
              style={btn("primary")}
            >
              {t("tools.save")}
            </button>
          </div>
        )}
      </div>
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

function btn(kind?: "primary" | "danger"): React.CSSProperties {
  const base: React.CSSProperties = {
    height: 28,
    padding: "0 14px",
    fontSize: pxToRem(13),
    borderRadius: "var(--radius-btn)",
    border: "1px solid var(--paper-3)",
    cursor: "pointer",
  };
  if (kind === "primary") {
    return { ...base, background: "var(--accent)", color: "var(--white)", borderColor: "var(--accent)" };
  }
  if (kind === "danger") {
    return { ...base, background: "var(--white)", color: "var(--danger, #b4413c)" };
  }
  return { ...base, background: "var(--white)", color: "var(--text-paper)" };
}
