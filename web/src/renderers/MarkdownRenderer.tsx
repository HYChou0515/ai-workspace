/**
 * F10 — Markdown renderer. Reads the file, renders via react-markdown with
 * GFM tables/strikethrough/task-lists, and exposes a pencil toggle that
 * swaps to a textarea editor with debounced autosave.
 */

import { useCallback } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { useFileService } from "../api/fileService";
import { MonacoEditor } from "../components/MonacoEditor";
import { useEditMode } from "../hooks/editMode";
import { useFileBuffer } from "../hooks/fileBuffer";
import { MarpDeck } from "./marp/MarpDeck";
import { isMarpDoc } from "./marp/marpDeck";

export function MarkdownRenderer({ path }: { path: string }) {
  // Content + edits live in the shared per-path buffer, so this file
  // opened in two split panes edits live on both sides. The Edit/Preview
  // toggle lives in the group tab strip (VSCode-style) via useEditMode.
  const { entry, setText, readOnly } = useFileBuffer(path);
  const { isEditing } = useEditMode();
  const svc = useFileService();
  // Stable across re-renders so <MarpDeck> memoises its render+shadow injection
  // (a fresh closure per render re-parsed the deck and wiped present-mode state).
  const resolveAsset = useCallback((src: string) => svc.fileUrl(src, path), [svc, path]);

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
  if (editing) {
    return (
      <div style={{ height: "100%", minHeight: 0 }}>
        <MonacoEditor value={text} onChange={setText} language="markdown" readOnly={readOnly} minHeight={0} />
      </div>
    );
  }

  // A Marp deck (frontmatter `marp: true`) renders as slides, not prose;
  // its workspace-relative images resolve through the same file API.
  if (isMarpDoc(text)) {
    return <MarpDeck text={text} resolveAsset={resolveAsset} />;
  }

  return (
    <article className="md-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          // Resolve workspace-relative image paths so `![](./xxx.png)` in
          // any markdown file (notes, drafts, brief.md, reports written
          // outside the F11 report viewer) lands on the file API.
          img: ({ src, alt }) => {
            if (!src) return null;
            const url = svc.fileUrl(src, path);
            return (
              <img src={url} alt={alt ?? ""} style={{ maxWidth: "100%", height: "auto" }} />
            );
          },
          // Same for links: `[abc.png](/step2-download/abc.png)` resolves to
          // the file API (opens the file) so the user can see its content;
          // external URLs / #fragments pass through.
          a: ({ href, children, ...rest }) => {
            const resolved = typeof href === "string" ? svc.fileUrl(href, path) : href;
            const isFile = typeof href === "string" && resolved !== href;
            return (
              <a href={resolved} {...rest} {...(isFile ? { target: "_blank", rel: "noreferrer" } : {})}>
                {children}
              </a>
            );
          },
        }}
      >
        {text}
      </ReactMarkdown>
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
