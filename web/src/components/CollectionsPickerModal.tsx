import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { FileService } from "../api/fileService";
import { kbApi, type KbApi } from "../api/kb";
import { qk } from "../api/queryKeys";
import { useItemCollections, COLLECTIONS_PATH } from "../hooks/useItemCollections";
import { serializeCollectionsFile, type CollectionEntry } from "./collectionsFile";
import { Icon, type IconName } from "./Icon";

/**
 * The collection-set picker (topic-hub §5, #142): a modal over an item's
 * `collections.json`. It lists every live KB collection as a checklist (search +
 * doc counts), pre-checked from the file, and writes the chosen set back as
 * `[{id,name}]` — using LIVE names so a rename self-heals, and preserving any
 * orphan id (deleted collection) verbatim until the user removes it.
 *
 * Persistence is the locked last-write-wins overwrite: open reads fresh, Save
 * overwrites the whole file (no merge) and invalidates both the picker's read
 * and the Monaco editor's, so an open `collections.json` tab refreshes. The file
 * is never written on open — only on an explicit Save.
 */
export function CollectionsPickerModal({
  fileService,
  onClose,
  client = kbApi,
}: {
  fileService: FileService;
  onClose: () => void;
  client?: KbApi;
}) {
  const qc = useQueryClient();
  const collQ = useQuery({ queryKey: qk.kb.collections, queryFn: () => client.listCollections() });
  const fileQ = useItemCollections(fileService);

  const [checked, setChecked] = useState<Set<string> | null>(null);
  const [initial, setInitial] = useState<Set<string> | null>(null);
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  // Seed the editable selection once the file has been read fresh on open.
  useEffect(() => {
    if (checked === null && fileQ.data) {
      setChecked(new Set(fileQ.data.selectedIds));
      setInitial(new Set(fileQ.data.selectedIds));
    }
  }, [checked, fileQ.data]);

  const available = collQ.data ?? [];
  const fileEntries = fileQ.data?.entries ?? [];
  const ready = checked !== null && initial !== null && fileQ.data !== undefined && collQ.data !== undefined;

  const dirty =
    !!checked &&
    !!initial &&
    (checked.size !== initial.size || [...checked].some((id) => !initial.has(id)));

  const toggle = (id: string) =>
    setChecked((prev) => {
      const next = new Set(prev ?? []);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const term = search.trim().toLowerCase();
  const visible = available.filter((c) => c.name.toLowerCase().includes(term));
  const orphans = checked
    ? fileEntries.filter((e) => checked.has(e.id) && !available.some((c) => c.resource_id === e.id))
    : [];

  const attemptClose = () => {
    if (dirty) setConfirming(true);
    else onClose();
  };

  const onSave = async () => {
    if (!checked) return;
    const out: CollectionEntry[] = [];
    for (const c of available) if (checked.has(c.resource_id)) out.push({ id: c.resource_id, name: c.name });
    // Orphans the user left in place are preserved verbatim (never auto-dropped).
    for (const e of fileEntries) {
      if (checked.has(e.id) && !available.some((c) => c.resource_id === e.id)) out.push(e);
    }
    setSaving(true);
    setSaveError(null);
    try {
      await fileService.writeFile(COLLECTIONS_PATH, serializeCollectionsFile(out));
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.itemCollections(fileService.scopeId) }),
        qc.invalidateQueries({ queryKey: qk.file(fileService.scopeId, COLLECTIONS_PATH) }),
      ]);
      onClose();
    } catch (e) {
      setSaveError((e as Error)?.message ?? "Save failed");
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
        aria-label="選擇知識庫"
        data-testid="collections-modal"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 460,
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
        <strong style={{ fontSize: 14 }}>選擇知識庫</strong>
        <p style={{ margin: 0, fontSize: 12, color: "var(--text-paper-d)", lineHeight: 1.5 }}>
          勾選這個主題要查詢的知識庫；選好才有內容可供 AI 檢索與引用。
        </p>

        {fileQ.data?.status === "invalid" && (
          <div
            data-testid="collections-invalid-banner"
            style={{
              fontSize: 12,
              lineHeight: 1.5,
              padding: "8px 10px",
              borderRadius: "var(--radius-btn)",
              border: "1px solid var(--danger, #b4413c)",
              color: "var(--danger, #b4413c)",
              background: "rgba(180,65,60,0.06)",
            }}
          >
            collections.json 目前無法解析（可能正在手動編輯）。下方以空清單顯示；按「儲存」會以乾淨清單覆寫原內容。
          </div>
        )}

        {ready && checked!.size === 0 && fileQ.data?.status !== "invalid" && (
          <div
            data-testid="collections-empty-hint"
            style={{ fontSize: 12, color: "var(--text-paper-d)" }}
          >
            尚未選擇任何知識庫。
          </div>
        )}

        <div style={{ position: "relative" }}>
          <input
            data-testid="collections-search"
            placeholder="搜尋知識庫…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              width: "100%",
              height: 30,
              boxSizing: "border-box",
              padding: "0 10px",
              fontSize: 13,
              borderRadius: "var(--radius-btn)",
              border: "1px solid var(--paper-3)",
              background: "var(--paper-1, var(--white))",
              color: "var(--text-paper)",
            }}
          />
        </div>

        <div style={{ overflowY: "auto", minHeight: 0, flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
          {!ready && <p style={{ fontSize: 12, color: "var(--text-paper-d)" }}>載入中…</p>}

          {fileQ.isError && (
            <p style={{ fontSize: 12, color: "var(--danger, #b4413c)" }}>無法讀取 collections.json。</p>
          )}

          {ready &&
            visible.map((c) => (
              <label
                key={c.resource_id}
                data-testid={`collection-row-${c.resource_id}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 6px",
                  borderRadius: "var(--radius-btn)",
                  cursor: "pointer",
                  fontSize: 13,
                }}
              >
                <input
                  type="checkbox"
                  data-testid={`collection-check-${c.resource_id}`}
                  checked={checked!.has(c.resource_id)}
                  onChange={() => toggle(c.resource_id)}
                />
                <Icon name={(c.icon || "layers") as IconName} size={15} color="var(--accent-h)" />
                <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {c.name}
                </span>
                <span style={{ fontSize: 11, color: "var(--text-paper-d)" }}>{c.doc_count} 份</span>
              </label>
            ))}

          {ready && visible.length === 0 && available.length > 0 && (
            <p style={{ fontSize: 12, color: "var(--text-paper-d)" }}>沒有符合「{search}」的知識庫。</p>
          )}

          {ready && available.length === 0 && (
            <p style={{ fontSize: 12, color: "var(--text-paper-d)" }}>目前沒有任何知識庫可選。</p>
          )}
        </div>

        {ready && (fileQ.data?.ignored ?? 0) > 0 && (
          <div data-testid="collections-ignored-note" style={{ fontSize: 11, color: "var(--text-paper-d)" }}>
            已忽略 {fileQ.data!.ignored} 筆無效項。
          </div>
        )}

        {orphans.length > 0 && (
          <div
            style={{
              borderTop: "1px solid var(--paper-3)",
              paddingTop: 8,
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            <span style={{ fontSize: 11, color: "var(--danger, #b4413c)" }}>已不存在的知識庫（建議移除）</span>
            {orphans.map((e) => (
              <div
                key={e.id}
                data-testid={`orphan-${e.id}`}
                style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}
              >
                <span style={{ flex: 1, minWidth: 0, color: "var(--text-paper-d)" }}>
                  {e.name || "(未命名)"} · <code style={{ fontSize: 11 }}>{e.id}</code>
                </span>
                <button
                  type="button"
                  data-testid={`orphan-remove-${e.id}`}
                  onClick={() => toggle(e.id)}
                  style={{
                    height: 24,
                    padding: "0 8px",
                    fontSize: 12,
                    borderRadius: "var(--radius-btn)",
                    border: "1px solid var(--paper-3)",
                    background: "var(--white)",
                    color: "var(--danger, #b4413c)",
                    cursor: "pointer",
                  }}
                >
                  移除
                </button>
              </div>
            ))}
          </div>
        )}

        {saveError && <div style={{ fontSize: 12, color: "var(--danger, #b4413c)" }}>{saveError}</div>}

        {confirming ? (
          <div
            data-testid="collections-discard-confirm"
            style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "flex-end" }}
          >
            <span style={{ flex: 1, fontSize: 12, color: "var(--text-paper-d)" }}>放棄未儲存的變更？</span>
            <button type="button" data-testid="discard-no" onClick={() => setConfirming(false)} style={btn()}>
              繼續編輯
            </button>
            <button type="button" data-testid="discard-yes" onClick={onClose} style={btn("danger")}>
              放棄變更
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 2 }}>
            <button type="button" data-testid="collections-cancel" onClick={attemptClose} style={btn()}>
              取消
            </button>
            <button
              type="button"
              data-testid="collections-save"
              onClick={onSave}
              disabled={!ready || !dirty || saving}
              style={btn("primary")}
            >
              {saving ? "儲存中…" : "儲存"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function btn(variant?: "primary" | "danger"): React.CSSProperties {
  const base: React.CSSProperties = {
    height: 30,
    padding: "0 14px",
    borderRadius: "var(--radius-btn)",
    fontSize: 13,
    cursor: "pointer",
    border: "1px solid var(--paper-3)",
    background: "var(--white)",
    color: "var(--text-paper)",
  };
  if (variant === "primary") {
    return {
      ...base,
      background: "var(--accent)",
      borderColor: "var(--accent)",
      color: "var(--white)",
    };
  }
  if (variant === "danger") {
    return { ...base, color: "var(--danger, #b4413c)", borderColor: "var(--danger, #b4413c)" };
  }
  return base;
}
