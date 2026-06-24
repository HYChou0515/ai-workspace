import { useT } from "../lib/i18n";
import { Icon } from "./Icon";

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
        fontSize: 12,
        borderRadius: "var(--radius-btn)",
        cursor: "pointer",
        border: `1px solid ${empty ? "var(--accent)" : "var(--paper-3)"}`,
        background: empty ? "var(--accent)" : "var(--white)",
        color: empty ? "var(--white)" : "var(--text-paper)",
        whiteSpace: "nowrap",
      }}
    >
      <Icon name="layers" size={12} color={empty ? "var(--white)" : "var(--text-paper-d)"} />
      <span>{label}</span>
    </button>
  );
}
