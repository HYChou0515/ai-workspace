/**
 * F10 — Markdown renderer. Reads the file, renders via react-markdown with
 * GFM tables/strikethrough/task-lists, and exposes a pencil toggle that
 * swaps to a textarea editor with debounced autosave.
 */

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Icon } from "../components/Icon";
import { useAutosave, useFileContent } from "../hooks/useFileContent";

export function MarkdownRenderer({
  investigationId,
  path,
}: {
  investigationId: string;
  path: string;
}) {
  const state = useFileContent(investigationId, path);

  if (state.kind === "loading") return <Status>Loading {path}…</Status>;
  if (state.kind === "error") return <Status tone="err">{state.error.message}</Status>;
  if (state.content.kind !== "text") {
    return <Status>Binary file — cannot display as markdown.</Status>;
  }

  return (
    <MarkdownBody
      investigationId={investigationId}
      path={path}
      initial={state.content.text}
    />
  );
}

function MarkdownBody({
  investigationId,
  path,
  initial,
}: {
  investigationId: string;
  path: string;
  initial: string;
}) {
  const { text, setText, status } = useAutosave(investigationId, path, initial);
  const [editing, setEditing] = useState(false);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          paddingBottom: 8,
          borderBottom: "1px solid var(--paper-3)",
        }}
      >
        <div className="caps">{path}</div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--text-paper-d2)",
          }}
        >
          <span aria-live="polite">{statusLabel(status)}</span>
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            style={{
              padding: "4px 10px",
              border: "1px solid var(--paper-3)",
              borderRadius: "var(--radius-btn)",
              fontSize: 12,
              color: editing ? "var(--accent)" : "var(--text-paper)",
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <Icon name="eye" size={12} />
            {editing ? "Preview" : "Edit"}
          </button>
        </div>
      </header>

      {editing ? (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          style={{
            width: "100%",
            minHeight: 360,
            border: "1px solid var(--paper-3)",
            borderRadius: "var(--radius-btn)",
            padding: 12,
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            lineHeight: 1.6,
            resize: "vertical",
            outline: "none",
            background: "var(--paper)",
          }}
        />
      ) : (
        <article className="md-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
        </article>
      )}

      <style>{MARKDOWN_CSS}</style>
    </div>
  );
}

function statusLabel(s: "clean" | "dirty" | "saving" | "saved" | "error"): string {
  switch (s) {
    case "clean":
      return "";
    case "dirty":
      return "unsaved…";
    case "saving":
      return "saving…";
    case "saved":
      return "autosaved";
    case "error":
      return "save failed";
  }
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
