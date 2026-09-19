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

import { useFileService, useOptionalFileService } from "../api/fileService";
import { MonacoEditor } from "../components/MonacoEditor";
import { useEditMode } from "../hooks/editMode";
import { useFileBuffer } from "../hooks/fileBuffer";
import { relPath } from "../lib/relPath";
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

  if (entry.status === "loading") return <Status>Loading {relPath(path)}…</Status>;
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

  return <MarkdownBody text={text} path={path} />;
}

/** Render markdown TEXT (not a file) with the workspace's own conventions: GFM
 * tables/task-lists, math, and relative image/link paths resolved against the
 * file they were written in. Split out of the file renderer so anything holding
 * markdown in memory — an entity record's body, say — reads exactly like the
 * same prose would in a `.md` file, instead of growing a second, poorer
 * markdown pipeline beside this one. `path` is the resolution base.
 *
 * One caller holds markdown that is NOT in a workspace: the onboarding modal,
 * on the Launcher and the AppDashboard, outside any `FileServiceProvider`.
 * Such a caller passes `resolveUrl` — how ITS refs become browser URLs — and
 * the same pipeline runs without a file service; when both are present the
 * caller's resolver wins, because it said so. A caller that passes neither
 * still needs the provider; the throw is deliberate (a silent identity
 * resolver would render broken images and say nothing). `compact` is the
 * chat-sized variant (`.md-compact`). `className` goes on the article beside
 * `md-body`, which is how a context overrides what `.md-body` sets — colour,
 * size — the way `.kb-msg__text.md-body` does. */
export function MarkdownBody({
  text,
  path,
  resolveUrl,
  compact = false,
  className,
}: {
  text: string;
  path?: string;
  resolveUrl?: (src: string) => string;
  compact?: boolean;
  className?: string;
}) {
  const svc = useOptionalFileService();
  if (!resolveUrl && !svc) {
    throw new Error("MarkdownBody needs a resolveUrl or a <FileServiceProvider>");
  }
  const resolve = resolveUrl ?? ((src: string) => svc!.fileUrl(src, path));
  const classes = [className, "md-body", compact && "md-compact"].filter(Boolean).join(" ");
  return (
    <article className={classes}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          // Resolve workspace-relative image paths so `![](./xxx.png)` in
          // any markdown file (notes, drafts, brief.md, reports written
          // outside the F11 report viewer) lands on the file API.
          img: ({ src, alt }) => {
            if (!src) return null;
            const url = resolve(src);
            return (
              <img src={url} alt={alt ?? ""} style={{ maxWidth: "100%", height: "auto" }} />
            );
          },
          // Same for links: `[abc.png](/step2-download/abc.png)` resolves to
          // the file API (opens the file) so the user can see its content;
          // external URLs / #fragments pass through.
          a: ({ href, children, ...rest }) => {
            const resolved = typeof href === "string" ? resolve(href) : href;
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
