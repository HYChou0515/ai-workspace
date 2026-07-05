import type { KbCollection } from "../../api/kb";
import { CollectionsChecklist } from "../../components/CollectionsChecklist";
import { ModalShell } from "../../components/ModalShell";
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
    <ModalShell
      onClose={onClose}
      ariaLabel={t("collections.kbTitle")}
      data-testid="kb-collections-dialog"
      width={460}
      maxWidth="92vw"
      panelStyle={{
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
            className="btn"
            data-variant="primary"
            data-size="sm"
            data-testid="kb-collections-done"
            onClick={onClose}
          >
            {t("picker.done")}
          </button>
        </div>
    </ModalShell>
  );
}
