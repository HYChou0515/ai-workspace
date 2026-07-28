/**
 * RetrievalToggles + WikiBadge — the design_handoff_rca_3.0 retrieval controls
 * (rca/views/wiki.jsx). Used by the new-collection modal and the collection
 * settings to pick how a collection answers: document search (chunk-RAG) and/or
 * the AI-maintained wiki. Controlled — the parent owns the state.
 */

import { Icon } from "../../components/Icon";
import { SettingRow } from "./SettingRow";
import { useT } from "../../lib/i18n";
import { pxToRem } from "../../lib/pxToRem";

export function RetrievalToggles({
  docSearch,
  wiki,
  onChange,
}: {
  docSearch: boolean;
  wiki: boolean;
  onChange: (next: { docSearch: boolean; wiki: boolean }) => void;
}) {
  const t = useT();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <SettingRow
        icon="search"
        title={t("kb.retrieval.docSearch")}
        recommended
        desc={t("kb.retrieval.docSearch.desc")}
        on={docSearch}
        onToggle={() => onChange({ docSearch: !docSearch, wiki })}
      />
      <SettingRow
        icon="layers"
        title={t("kb.retrieval.wiki")}
        desc={t("kb.retrieval.wiki.desc")}
        on={wiki}
        onToggle={() => onChange({ docSearch, wiki: !wiki })}
      />
      {docSearch && wiki && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 12px",
            background: "var(--accent-soft)",
            borderRadius: 6,
          }}
        >
          <Icon name="sparkle" size={13} color="var(--accent-h)" />
          <span style={{ fontSize: pxToRem(12), color: "var(--ink)" }}>{t("kb.retrieval.both")}</span>
        </div>
      )}
    </div>
  );
}

/** A compact "Wiki" badge for collection cards (the collection builds a wiki). */
export function WikiBadge() {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 7px",
        borderRadius: 4,
        background: "var(--ink)",
        color: "var(--paper)",
        fontFamily: "var(--font-mono)",
        fontSize: pxToRem(10),
        fontWeight: 500,
      }}
    >
      <Icon name="wiki" size={10} color="var(--accent)" /> Wiki
    </span>
  );
}
