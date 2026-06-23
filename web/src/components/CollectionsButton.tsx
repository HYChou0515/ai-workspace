import { Icon } from "./Icon";

/**
 * The Topic Hub top-bar entry to the collection-set picker (#142). Two states:
 * an empty selection nudges the user with an accent-styled "選擇知識庫" (a brand-new
 * Hub has nothing for the agent to retrieve); a non-empty one shows a quiet
 * "知識庫 (N)" badge. Presentational — the shell owns the count + opens the modal.
 */
export function CollectionsButton({ count, onClick }: { count: number; onClick: () => void }) {
  const empty = count === 0;
  return (
    <button
      type="button"
      data-testid="collections-button"
      className={`collections-button${empty ? " collections-button--empty" : ""}`}
      aria-label="選擇知識庫"
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
      <span>{empty ? "選擇知識庫" : `知識庫 (${count})`}</span>
    </button>
  );
}
