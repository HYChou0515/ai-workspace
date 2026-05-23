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
      <style>{MARKDOWN_CSS}</style>
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

/* Inline stylesheet — applies design typography to rendered markdown. */
const MARKDOWN_CSS = `
.md-body { color: var(--text-paper); font-size: var(--text-body-lg); line-height: var(--leading-body-lg); max-width: 760px; }
.md-body h1, .md-body h2, .md-body h3 { font-family: var(--font-display); font-weight: 800; letter-spacing: -0.02em; margin: 24px 0 8px; line-height: 1.15; }
.md-body h1 { font-size: var(--text-display-md); }
.md-body h2 { font-size: var(--text-display-sm); }
.md-body h3 { font-size: 18px; }
.md-body p { margin: 0 0 12px; }
.md-body ul, .md-body ol { padding-left: 22px; margin: 0 0 12px; }
.md-body li { margin: 4px 0; }
.md-body code { font-family: var(--font-mono); background: var(--paper-2); padding: 1px 4px; border-radius: 3px; font-size: 0.9em; }
.md-body pre { font-family: var(--font-mono); background: var(--paper-2); padding: 12px; border-radius: 6px; overflow: auto; font-size: 13px; }
.md-body pre code { background: transparent; padding: 0; }
.md-body table { border-collapse: collapse; width: 100%; margin: 0 0 12px; }
.md-body th, .md-body td { border: 1px solid var(--paper-3); padding: 6px 10px; text-align: left; }
.md-body th { background: var(--paper-2); font-weight: 600; }
.md-body blockquote { border-left: 3px solid var(--accent); padding-left: 12px; color: var(--text-paper-d); margin: 0 0 12px; }
.md-body a { color: var(--accent-h); text-decoration: underline; }
.md-body hr { border: 0; border-top: 1px solid var(--paper-3); margin: 24px 0; }
`;
