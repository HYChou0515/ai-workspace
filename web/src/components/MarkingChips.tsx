/**
 * #847 P7: named markings as chips — above the composer (each removable, so a
 * selection goes with a message only when the user leaves it there) and on the
 * sent message in the thread, where a refused one says why it was not sent.
 */
import type { SentMarking } from "../api/types";
import { useOpenFile, useWorkspaceVisible } from "../hooks/openFile";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { Icon } from "./Icon";

export function MarkingChips({
  markings,
  onRemove,
}: {
  markings: readonly SentMarking[];
  /** The composer's remove control; absent in the thread. */
  onRemove?: (name: string) => void;
}) {
  const t = useT();
  const opener = useOpenFile();
  const openFile = useWorkspaceVisible() ? opener : null;
  if (markings.length === 0) return null;
  return (
    <div
      data-testid="marking-chips"
      style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}
    >
      {markings.map((m) => {
        const refused = Boolean(m.error);
        const counts = Object.entries(m.counts)
          .map(([c, n]) => `${c} ${n}`)
          .join(" · ");
        const label = (
          <>
            <Icon name="tag" size={11} color={refused ? "var(--err)" : "var(--text-paper-d)"} />
            <span style={{ fontWeight: 600 }}>{m.name}</span>
            <span style={{ color: "var(--text-paper-d)" }}>{counts}</span>
            {refused && (
              <span style={{ color: "var(--err)" }}>
                {t("markings.notSent")}: {m.error}
              </span>
            )}
          </>
        );
        const canOpen = Boolean(openFile && m.path && !onRemove);
        return (
          <span
            key={m.name}
            data-testid="marking-chip"
            data-refused={refused ? "true" : "false"}
            title={m.error ?? m.path ?? undefined}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              maxWidth: "100%",
              padding: "2px 8px",
              borderRadius: 999,
              border: `1px solid ${refused ? "var(--err)" : "var(--paper-3)"}`,
              background: "var(--white)",
              fontSize: pxToRem(11),
              color: "var(--text-paper)",
            }}
          >
            {canOpen ? (
              <button
                type="button"
                onClick={() => openFile!(m.path)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  background: "none",
                  border: "none",
                  padding: 0,
                  font: "inherit",
                  color: "inherit",
                  cursor: "pointer",
                }}
              >
                {label}
              </button>
            ) : (
              label
            )}
            {onRemove && (
              <button
                type="button"
                aria-label={t("markings.remove", { name: m.name })}
                onClick={() => onRemove(m.name)}
                style={{
                  display: "inline-flex",
                  background: "none",
                  border: "none",
                  padding: 0,
                  cursor: "pointer",
                  color: "var(--text-paper-d)",
                }}
              >
                <Icon name="x" size={11} />
              </button>
            )}
          </span>
        );
      })}
    </div>
  );
}
