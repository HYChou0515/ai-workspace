/**
 * The persistent "?" help entry (#161). Reopens the current surface's welcome
 * teaching — so "Don't show again" only stops the auto-popup, never hides the
 * teaching for good. Shared by the Launcher, App dashboards, and the workspace.
 */

export function HelpButton({
  onClick,
  label = "Help",
}: {
  onClick: () => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      style={{
        width: 26,
        height: 26,
        borderRadius: 999,
        border: "1px solid var(--paper-3)",
        background: "var(--white)",
        color: "var(--text-paper-d)",
        fontSize: 14,
        fontWeight: 700,
        lineHeight: 1,
        cursor: "pointer",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
      }}
    >
      ?
    </button>
  );
}
