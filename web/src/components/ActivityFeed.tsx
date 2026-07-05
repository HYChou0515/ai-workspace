/**
 * ActivityFeed (#455 P3) — a per-item collaboration timeline. Scopes the global
 * activity log to one work item and renders it newest-first; a row that points at
 * an openable file (an entity record write, a file event) is a button that opens
 * that file in the IDE, so a teammate's change is one click away. Rows with no
 * openable target (item created, agent turn) render as plain text rather than a
 * dead control.
 *
 * The activity log is best-effort + per-pod (not persisted) — a restart / other
 * pod starts it empty, same as the notifications popover it shares.
 */

import { relativeTime } from "../api/types";
import type { OpenFile } from "../hooks/openFile";
import { useEntityCatalog } from "../hooks/useEntities";
import { useActivity } from "../hooks/useResources";
import { pxToRem } from "../lib/pxToRem";
import { activityOpenTarget, filterItemActivity } from "../lib/activityFeed";

const rowText: React.CSSProperties = { fontSize: pxToRem(13), color: "var(--text-paper)" };
const rowTime: React.CSSProperties = { fontSize: pxToRem(11), color: "var(--text-paper-d)", marginTop: 2 };
const rowBase: React.CSSProperties = {
  display: "block",
  width: "100%",
  textAlign: "left",
  padding: "6px 12px",
  borderBottom: "1px solid var(--paper-3)",
};

export function ActivityFeed({ slug, itemId, onOpenFile }: { slug: string; itemId: string; onOpenFile: OpenFile }) {
  const catalog = useEntityCatalog(slug, itemId).data;
  const entries = filterItemActivity(useActivity(), itemId);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--paper)" }}>
      <div className="caps" style={{ padding: "8px 12px", color: "var(--text-paper-d)" }}>
        Activity
      </div>
      <div className="scrollable" style={{ flex: 1, overflowY: "auto" }}>
        {entries.length === 0 ? (
          <div style={{ padding: 12, fontSize: pxToRem(13), color: "var(--text-paper-d)" }}>No activity yet.</div>
        ) : (
          entries.map((entry, i) => {
            const target = activityOpenTarget(entry, catalog);
            const body = (
              <>
                <div style={rowText}>{entry.text}</div>
                <div style={rowTime}>{relativeTime(entry.ts)}</div>
              </>
            );
            return target ? (
              <button
                key={i}
                type="button"
                onClick={() => onOpenFile(target, { preview: true })}
                style={{ ...rowBase, background: "transparent", border: "none", cursor: "pointer" }}
              >
                {body}
              </button>
            ) : (
              <div key={i} style={rowBase}>
                {body}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
