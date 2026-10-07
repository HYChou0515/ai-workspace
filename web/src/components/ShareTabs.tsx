/**
 * The tab strip a share dialog uses to keep People and Groups out of each
 * other's way. One panel holding a people picker, a people list, a group picker
 * and a group list spends more vertical budget than a laptop has: eight granted
 * people pushed the Groups section 253px past the bottom of the panel, where it
 * was reachable only by scrolling and easy to miss entirely. Each side gets its
 * own tab instead, and each tab carries its grant count so the side you are not
 * looking at is never a blind spot.
 */
import { pxToRem } from "../lib/pxToRem";

export type ShareTab = { id: string; label: string; count: number };

export function ShareTabs({
  tabs,
  value,
  onChange,
  idPrefix = "share-tab",
  ariaLabel = "Share with",
}: {
  tabs: ShareTab[];
  value: string;
  onChange: (id: string) => void;
  /** The tab ids and test ids: `{idPrefix}-{tab id}`. The Env panel uses the
   * same strip, so the two dialogs' tabs look alike (plan-personal-env A22). */
  idPrefix?: string;
  ariaLabel?: string;
}) {
  return (
    <div role="tablist" aria-label={ariaLabel} style={strip}>
      {tabs.map((t) => {
        const on = t.id === value;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`${idPrefix}-${t.id}`}
            aria-selected={on}
            aria-controls={idPrefix === "share-tab" ? `share-panel-${t.id}` : undefined}
            data-testid={`${idPrefix}-${t.id}`}
            onClick={() => onChange(t.id)}
            style={{
              ...tab,
              color: on ? "var(--text-paper)" : "var(--text-paper-d)",
              borderBottomColor: on ? "var(--accent)" : "transparent",
            }}
          >
            {t.label}
            {t.count > 0 && <span style={badge}>{t.count}</span>}
          </button>
        );
      })}
    </div>
  );
}

const strip: React.CSSProperties = {
  display: "flex",
  gap: 4,
  borderBottom: "1px solid var(--paper-3)",
};
const tab: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "6px 10px",
  background: "transparent",
  fontSize: pxToRem(12.5),
  cursor: "pointer",
  borderBottom: "2px solid transparent",
  marginBottom: -1,
};
const badge: React.CSSProperties = {
  minWidth: 16,
  padding: "0 5px",
  borderRadius: 8,
  background: "var(--paper-3)",
  color: "var(--text-paper-d)",
  fontSize: pxToRem(10.5),
  textAlign: "center",
};
