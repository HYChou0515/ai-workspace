/**
 * Generic text renderer for csv / json / yaml / plain text — Monaco with
 * the right language, editing through the shared file buffer (so it
 * autosaves and syncs across split panes like every other file).
 */

import { MonacoEditor, monacoLanguage } from "../components/MonacoEditor";
import { useFileBuffer } from "../hooks/fileBuffer";

export function TextRenderer({ path }: { investigationId: string; path: string }) {
  const { entry, setText } = useFileBuffer(path);

  if (entry.status === "loading") return <Status>Loading {path}…</Status>;
  if (entry.status === "error") {
    return <Status tone="err">{entry.error ?? "load failed"}</Status>;
  }
  // Every file is editable: text loads as UTF-8, binary as byte-exact
  // latin1 (entry.encoding), so even a true binary can be opened here.

  // No path/status header — the pane breadcrumb shows the path and the tab
  // shows the dirty dot. The editor fills the whole pane.
  return (
    <div style={{ height: "100%", minHeight: 0 }}>
      <MonacoEditor
        value={entry.text}
        onChange={setText}
        language={monacoLanguage(path)}
        minimap
        minHeight={0}
      />
    </div>
  );
}

function Status({ children, tone = "muted" }: { children: React.ReactNode; tone?: "muted" | "err" }) {
  return (
    <div style={{ color: tone === "err" ? "var(--err)" : "var(--text-paper-d)", fontSize: "var(--text-body)" }}>
      {children}
    </div>
  );
}
