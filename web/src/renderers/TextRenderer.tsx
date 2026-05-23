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
  if (entry.kind !== "text") {
    return <Status>Binary file — {entry.size} bytes.</Status>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, height: "100%" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div className="caps">{path}</div>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--text-paper-d2)",
          }}
        >
          {saveLabel(entry.save)}
        </span>
      </div>
      <div style={{ flex: 1, minHeight: 360 }}>
        <MonacoEditor
          value={entry.text}
          onChange={setText}
          language={monacoLanguage(path)}
          minimap
          minHeight={360}
        />
      </div>
    </div>
  );
}

function saveLabel(s: string): string {
  return s === "saving" ? "saving…" : s === "saved" ? "autosaved" : s === "dirty" ? "unsaved…" : "";
}

function Status({ children, tone = "muted" }: { children: React.ReactNode; tone?: "muted" | "err" }) {
  return (
    <div style={{ color: tone === "err" ? "var(--err)" : "var(--text-paper-d)", fontSize: "var(--text-body)" }}>
      {children}
    </div>
  );
}
