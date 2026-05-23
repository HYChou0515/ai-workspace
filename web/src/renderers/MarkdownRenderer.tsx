/**
 * F10 — Markdown renderer. Reads the file, renders via react-markdown with
 * GFM tables/strikethrough/task-lists, and exposes a pencil toggle that
 * swaps to a textarea editor with debounced autosave.
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { MonacoEditor } from "../components/MonacoEditor";
import { useEditMode } from "../hooks/editMode";
import { useFileBuffer } from "../hooks/fileBuffer";

export function MarkdownRenderer({ path }: { investigationId: string; path: string }) {
  // Content + edits live in the shared per-path buffer, so this file
  // opened in two split panes edits live on both sides. The Edit/Preview
  // toggle lives in the group tab strip (VSCode-style) via useEditMode.
  const { entry, setText } = useFileBuffer(path);
  const { isEditing } = useEditMode();

  if (entry.status === "loading") return <Status>Loading {path}…</Status>;
  if (entry.status === "error") {
    return <Status tone="err">{entry.error ?? "load failed"}</Status>;
  }
  if (entry.kind !== "text") {
    return <Status>Binary file — cannot display as markdown.</Status>;
  }

  const text = entry.text;
  const editing = isEditing(path);

  // Editing fills the pane (Monaco scrolls internally); preview flows and
  // the pane scrolls. The path lives in the breadcrumb, not here.
  return editing ? (
    <div style={{ height: "100%", minHeight: 0 }}>
      <MonacoEditor value={text} onChange={setText} language="markdown" minHeight={0} />
    </div>
  ) : (
    <article className="md-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </article>
  );
}

function Status({
  children,
  tone = "muted",
}: {
  children: React.ReactNode;
  tone?: "muted" | "err";
}) {
  return (
    <div
      style={{
        color: tone === "err" ? "var(--err)" : "var(--text-paper-d)",
        fontSize: "var(--text-body)",
      }}
    >
      {children}
    </div>
  );
}
