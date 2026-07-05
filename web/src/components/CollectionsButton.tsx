import { useT } from "../lib/i18n";
import { Icon } from "./Icon";
import { pxToRem } from "../lib/pxToRem";

/**
 * The Topic Hub top-bar entry to the collection-set picker (#142). Two states:
 * an empty selection nudges with an accent-styled "set search scope" (a brand-
 * new Hub has nothing for the agent to retrieve); a non-empty one frames the
 * selection as the agent's *search scope* — "搜尋範圍 · N" — rather than a
 * context-free count, with a tooltip spelling out what it controls (#172).
 * Presentational — the shell owns the count + opens the modal.
 */
export function CollectionsButton({ count, onClick }: { count: number; onClick: () => void }) {
  const t = useT();
  const empty = count === 0;
  const label = empty ? t("collections.set") : t("collections.scope", { n: count });
  return (
    <button
      type="button"
      data-testid="collections-button"
      className={`collections-button${empty ? " collections-button--empty" : ""}`}
      aria-label={label}
      title={t("collections.tip")}
      onClick={onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        height: 26,
        padding: "0 10px",
        fontSize: pxToRem(12),
        borderRadius: "var(--radius-btn)",
        cursor: "pointer",
        border: `1px solid ${empty ? "var(--accent)" : "var(--paper-3)"}`,
        // Empty = a soft-accent NUDGE (tint + accent border/text), not a solid
        // --accent fill — that weight is reserved for the page's primary action
        // (#466). Matches the .btn[data-active] soft-accent convention.
        background: empty ? "var(--accent-soft)" : "var(--white)",
        color: empty ? "var(--accent)" : "var(--text-paper)",
        whiteSpace: "nowrap",
      }}
    >
      <Icon name="layers" size={12} color={empty ? "var(--accent)" : "var(--text-paper-d)"} />
      <span>{label}</span>
    </button>
  );
}
