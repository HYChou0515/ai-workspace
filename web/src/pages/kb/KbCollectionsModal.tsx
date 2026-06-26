import type { KbCollection } from "../../api/kb";
import { CollectionsChecklist } from "../../components/CollectionsChecklist";
import { useT } from "../../lib/i18n";
import { pxToRem } from "../../lib/pxToRem";

/**
 * The KB chat collection modal (#271): the full collection set behind the pill
 * shortlist. Same checklist (search + select-all + rows) as the topic-hub picker
 * — the shared `CollectionsChecklist` — so the two look identical. Unlike the
 * topic-hub picker there is no file to write, so edits apply LIVE (each toggle
 * flows straight to `onChange`); "Done" just closes. No dirty-guard, no save.
 */
export function KbCollectionsModal({
  collections,
  selected,
  onChange,
  onClose,
}: {
  collections: KbCollection[];
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
  onClose: () => void;
}) {
  const t = useT();
  return (
    <div
      role="presentation"
      data-testid="kb-collections-backdrop"
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
        aria-label={t("collections.kbTitle")}
        data-testid="kb-collections-dialog"
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
        <strong style={{ fontSize: pxToRem(14) }}>{t("collections.kbTitle")}</strong>
        <p style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
          {t("collections.kbDesc")}
        </p>

        <CollectionsChecklist collections={collections} selected={selected} onChange={onChange} />

        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 2 }}>
          <button
            type="button"
            data-testid="kb-collections-done"
            onClick={onClose}
            style={{
              height: 30,
              padding: "0 14px",
              borderRadius: "var(--radius-btn)",
              fontSize: pxToRem(13),
              cursor: "pointer",
              border: "1px solid var(--accent)",
              background: "var(--accent)",
              color: "var(--white)",
            }}
          >
            {t("picker.done")}
          </button>
        </div>
      </div>
    </div>
  );
}
