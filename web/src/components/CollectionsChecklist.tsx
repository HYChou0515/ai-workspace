import { useState } from "react";

import type { KbCollection } from "../api/kb";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { Icon, type IconName } from "./Icon";

/**
 * The shared collection checklist (#271): a search box + select-all/clear bar +
 * a scrollable list of live KB collections as checkboxes (icon, name, doc count).
 * Purely presentational and controlled — the caller owns the selected `Set` and
 * applies whatever `onChange` hands back. Both the topic-hub picker modal (which
 * persists to `collections.json`) and the KB chat collection modal (which keeps
 * an in-memory selection) render this, so the two look identical.
 *
 * Search is a local view concern, so it lives here. Select-all / clear act on
 * the *currently filtered* rows (union / difference against the live selection),
 * which makes "filter, then select all matches" the obvious gesture.
 */
export function CollectionsChecklist({
  collections,
  selected,
  onChange,
}: {
  collections: KbCollection[];
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  const t = useT();
  const [search, setSearch] = useState("");
  const term = search.trim().toLowerCase();
  const visible = collections.filter((c) => c.name.toLowerCase().includes(term));

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange(next);
  };
  const selectAll = () => {
    const next = new Set(selected);
    for (const c of visible) next.add(c.resource_id);
    onChange(next);
  };
  const clear = () => {
    const next = new Set(selected);
    for (const c of visible) next.delete(c.resource_id);
    onChange(next);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, flex: 1, minHeight: 0 }}>
      <input
        data-testid="collections-search"
        placeholder={t("collections.search")}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        style={{
          width: "100%",
          height: 30,
          boxSizing: "border-box",
          padding: "0 10px",
          fontSize: pxToRem(13),
          borderRadius: "var(--radius-btn)",
          border: "1px solid var(--paper-3)",
          background: "var(--paper-1, var(--white))",
          color: "var(--text-paper)",
        }}
      />

      {collections.length > 0 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button type="button" data-testid="collections-select-all" onClick={selectAll} style={linkBtn()}>
            {t("collections.selectAll")}
          </button>
          <button type="button" data-testid="collections-clear" onClick={clear} style={linkBtn()}>
            {t("collections.clear")}
          </button>
        </div>
      )}

      <div
        style={{
          overflowY: "auto",
          minHeight: 0,
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        {visible.map((c) => (
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
              fontSize: pxToRem(13),
            }}
          >
            <input
              type="checkbox"
              data-testid={`collection-check-${c.resource_id}`}
              checked={selected.has(c.resource_id)}
              onChange={() => toggle(c.resource_id)}
            />
            <Icon name={(c.icon || "layers") as IconName} size={15} color="var(--accent-h)" />
            <span
              style={{
                flex: 1,
                minWidth: 0,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {c.name}
            </span>
            <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
              {t("collections.docCount", { n: c.doc_count })}
            </span>
          </label>
        ))}

        {collections.length > 0 && visible.length === 0 && (
          <p style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
            {t("collections.noMatch", { q: search.trim() })}
          </p>
        )}
        {collections.length === 0 && (
          <p style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>{t("collections.none")}</p>
        )}
      </div>
    </div>
  );
}

function linkBtn(): React.CSSProperties {
  return {
    height: 24,
    padding: "0 8px",
    fontSize: pxToRem(12),
    borderRadius: "var(--radius-btn)",
    border: "1px solid var(--paper-3)",
    background: "var(--white)",
    color: "var(--text-paper)",
    cursor: "pointer",
  };
}
