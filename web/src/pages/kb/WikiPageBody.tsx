/**
 * WikiPageBody — renders ONE wiki page's markdown the wiki way: [[wikilink]]s
 * become in-app navigation, and the trailing `Sources:` line becomes a
 * clickable footer that opens the underlying document. Extracted from
 * WikiBrowser so the editable wiki IDE (#D) keeps the same reading experience
 * in its preview pane.
 */

import { useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { Icon } from "../../components/Icon";
import { baseAwareUrlTransform } from "../../renderers/mdUrlTransform";

// Keep wiki:// links intact for [[wikilink]] navigation; root-relative URLs the
// BE embeds (e.g. `/blobs/...`) get the deploy sub-path prepended (#73).
const urlTransform = baseAwareUrlTransform("wiki://");

/** Basename without extension — the [[wikilink]] target a page is addressed by. */
export const stem = (path: string) => (path.split("/").pop() ?? path).replace(/\.[^.]+$/, "");

/** A page's display title: prettified stem ("reflow-zone-3" → "Reflow Zone 3"). */
export const prettify = (path: string) =>
  stem(path)
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());

/** Split a page's trailing `Sources:` line off the body so we can render it as a
 * clickable footer (the page convention ends every page with one). */
export function splitSources(md: string): { body: string; sources: string[] } {
  const lines = md.replace(/\s+$/, "").split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const m = /^\s*Sources?:\s*(.+)$/i.exec(lines[i]);
    if (m) {
      const sources = m[1]
        .split(/[·,;]/)
        .map((s) => s.trim())
        .filter(Boolean);
      return { body: lines.slice(0, i).join("\n").replace(/\s+$/, ""), sources };
    }
    if (lines[i].trim() !== "") break; // only a trailing Sources line counts
  }
  return { body: md, sources: [] };
}

export function linkifyWikilinks(md: string): string {
  return md.replace(/\[\[([^\]]+)\]\]/g, (_m, name: string) => {
    const t = name.trim();
    return `[${t}](wiki://${encodeURIComponent(t)})`;
  });
}

export function WikiPageBody({
  content,
  pages,
  onNavigate,
  docIdByPath,
  onOpenDoc,
}: {
  content: string;
  /** All page paths, so a [[wikilink]] can tell whether its target exists. */
  pages: string[];
  /** Follow a [[wikilink]] to the page whose stem matches `name`. */
  onNavigate: (name: string) => void;
  /** Source path → document id, for the clickable Sources footer. */
  docIdByPath: Map<string, string>;
  onOpenDoc?: (documentId: string) => void;
}) {
  const { body, sources } = useMemo(() => splitSources(content), [content]);
  const components = useMemo<Components>(
    () => ({
      a({ href, children }) {
        if (href?.startsWith("wiki://")) {
          const name = decodeURIComponent(href.slice("wiki://".length));
          const exists = pages.some((p) => stem(p) === name);
          return (
            <button
              type="button"
              className="kb-wikilink"
              onClick={() => onNavigate(name)}
              style={{
                background: "none",
                border: 0,
                padding: 0,
                cursor: exists ? "pointer" : "default",
                color: exists ? "var(--accent-h)" : "var(--text-paper-d2)",
                textDecoration: "underline",
                textUnderlineOffset: "2px",
                fontWeight: 500,
                font: "inherit",
              }}
            >
              {children}
            </button>
          );
        }
        return (
          <a href={href} target="_blank" rel="noreferrer">
            {children}
          </a>
        );
      },
    }),
    [pages, onNavigate],
  );

  return (
    <>
      <article className="md-body kb-wiki__page">
        <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={urlTransform} components={components}>
          {linkifyWikilinks(body)}
        </ReactMarkdown>
      </article>

      {sources.length > 0 && (
        <div style={{ marginTop: 26, paddingTop: 16, borderTop: "1px solid var(--paper-3)" }}>
          <div className="caps" style={{ marginBottom: 10, fontSize: 11, color: "var(--text-paper-d2)" }}>
            Sources
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {sources.map((s) => {
              const id = docIdByPath.get(s);
              const openable = id != null && onOpenDoc != null;
              return (
                <button
                  key={s}
                  type="button"
                  disabled={!openable}
                  onClick={() => id && onOpenDoc?.(id)}
                  title={openable ? `Open ${s}` : s}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 7,
                    padding: "5px 10px 5px 8px",
                    background: "var(--white)",
                    border: "1px solid var(--paper-3)",
                    borderRadius: 6,
                    cursor: openable ? "pointer" : "default",
                    font: "inherit",
                    fontSize: 12,
                    fontFamily: "var(--font-mono)",
                    color: "var(--text-paper)",
                  }}
                >
                  <Icon name="file" size={13} color="var(--ink)" />
                  {s}
                  {openable && <Icon name="arrow_u" size={11} color="var(--text-paper-d2)" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}
