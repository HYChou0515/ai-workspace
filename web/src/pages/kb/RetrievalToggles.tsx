/**
 * RetrievalToggles + WikiBadge — the design_handoff_rca_3.0 retrieval controls
 * (rca/views/wiki.jsx). Used by the new-collection modal and the collection
 * settings to pick how a collection answers: document search (chunk-RAG) and/or
 * the AI-maintained wiki. Controlled — the parent owns the state.
 */

import { Icon } from "../../components/Icon";
import { useT } from "../../lib/i18n";
import { pxToRem } from "../../lib/pxToRem";

function Switch({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={onClick}
      style={{
        width: 40,
        height: 22,
        borderRadius: 11,
        border: 0,
        padding: 0,
        background: on ? "var(--accent)" : "var(--paper-3)",
        position: "relative",
        cursor: "pointer",
        flexShrink: 0,
        transition: "background .15s",
      }}
    >
      <span
        style={{
          position: "absolute",
          top: 2,
          left: on ? 20 : 2,
          width: 18,
          height: 18,
          borderRadius: "50%",
          background: "var(--white)",
          transition: "left .15s",
          boxShadow: "0 1px 2px rgba(0,0,0,.15)",
        }}
      />
    </button>
  );
}

function Row({
  icon,
  title,
  desc,
  on,
  recommended,
  onToggle,
}: {
  icon: "search" | "layers";
  title: string;
  desc: string;
  on: boolean;
  recommended?: boolean;
  onToggle: () => void;
}) {
  const t = useT();
  return (
    <div
      style={{
        display: "flex",
        gap: 12,
        padding: 14,
        background: "var(--white)",
        border: "1px solid var(--paper-3)",
        borderRadius: 8,
        alignItems: "flex-start",
      }}
    >
      <div
        style={{
          width: 32,
          height: 32,
          borderRadius: "var(--radius-btn)",
          background: on ? "var(--accent-soft)" : "var(--paper-2)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Icon name={icon} size={16} color={on ? "var(--accent-h)" : "var(--text-paper-d)"} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
          <span style={{ fontSize: pxToRem(14), fontWeight: 600, color: "var(--ink)" }}>{title}</span>
          {recommended && (
            <span
              style={{
                fontSize: pxToRem(10),
                fontWeight: 600,
                color: "var(--ok)",
                background: "color-mix(in srgb, var(--ok) 14%, transparent)",
                padding: "1px 6px",
                borderRadius: 4,
              }}
            >
              {t("kb.retrieval.recommended")}
            </span>
          )}
        </div>
        <div style={{ fontSize: pxToRem(12.5), color: "var(--text-paper-d)", lineHeight: 1.5 }}>{desc}</div>
      </div>
      <Switch on={on} onClick={onToggle} label={title} />
    </div>
  );
}

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
      <Row
        icon="search"
        title={t("kb.retrieval.docSearch")}
        recommended
        desc={t("kb.retrieval.docSearch.desc")}
        on={docSearch}
        onToggle={() => onChange({ docSearch: !docSearch, wiki })}
      />
      <Row
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
