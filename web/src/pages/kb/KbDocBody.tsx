/**
 * Shared document renderer used by both the citation drawer (KbDocViewer) and
 * the dedicated page (KbDocPage): fetches a rendered document, shows the cited
 * passage as a highlighted callout, highlights it in place, and turns kb://
 * body links into in-app navigation (the parent decides where — drawer swaps
 * in place, page pushes a route).
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown, {
  type Components,
  type Options,
  defaultUrlTransform,
} from "react-markdown";
import remarkGfm from "remark-gfm";

import { kbApi, type KbApi, type KbRenderedDoc } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { parseKbDocHref } from "./kbLinks";
import { rehypeHighlightSnippet } from "./rehypeHighlightSnippet";

// Keep kb:// links intact (default sanitization would drop the unknown
// scheme); everything else goes through the default.
const urlTransform = (url: string) =>
  url.startsWith("kb://") ? url : defaultUrlTransform(url);

export function KbDocBody({
  documentId,
  snippet,
  onNavigate,
  onLoaded,
  showChunks = false,
  client = kbApi,
}: {
  documentId: string;
  snippet?: string;
  /** A kb:// link in the body was followed → the target document id. */
  onNavigate: (targetId: string) => void;
  /** The rendered document finished loading (so chrome can show its name). */
  onLoaded?: (doc: KbRenderedDoc) => void;
  /** Offer a File ⇄ Chunks toggle (the indexed-chunks debug view). */
  showChunks?: boolean;
  client?: KbApi;
}) {
  const [view, setView] = useState<"file" | "chunks">("file");
  const { data: doc, error: queryError } = useQuery({
    queryKey: qk.kb.doc(documentId),
    queryFn: () => client.renderDocument(documentId),
  });
  // Fetch chunks eagerly when the toggle is offered so its label can show the
  // count before the user switches views.
  const { data: chunks = [] } = useQuery({
    queryKey: qk.kb.docChunks(documentId),
    queryFn: () => client.getDocChunks(documentId),
    enabled: showChunks,
  });
  const error = queryError
    ? queryError instanceof Error
      ? queryError.message
      : String(queryError)
    : null;

  // Notify the parent (chrome shows the doc name) once the render resolves.
  useEffect(() => {
    if (doc) onLoaded?.(doc);
    // onLoaded is intentionally excluded — callers pass inline fns.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc]);

  // Memoize so the parent's onLoaded-driven re-render doesn't hand ReactMarkdown
  // a fresh `a` component each time — that would remount the rendered links
  // (and detach any node a click is mid-flight on).
  const components = useMemo<Components>(
    () => ({
      a: ({ href, children }) => {
        const target = href ? parseKbDocHref(href) : null;
        if (target) {
          return (
            <button
              type="button"
              className="kb-doclink"
              onClick={() => onNavigate(target)}
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
    [onNavigate],
  );
  const rehypePlugins = useMemo<Options["rehypePlugins"]>(
    () => (snippet ? [[rehypeHighlightSnippet, snippet]] : []),
    [snippet],
  );

  return (
    <>
      {showChunks && (
        <div className="kb-docview-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={view === "file"}
            className={`kb-docview-tab${view === "file" ? " is-active" : ""}`}
            onClick={() => setView("file")}
          >
            <Icon name="file" size={13} /> File
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "chunks"}
            className={`kb-docview-tab${view === "chunks" ? " is-active" : ""}`}
            onClick={() => setView("chunks")}
          >
            <Icon name="layers" size={13} /> Chunks ({chunks.length})
          </button>
        </div>
      )}
      {snippet && view === "file" && (
        <div className="kb-docviewer__cited">
          <div className="kb-cites__label">Cited passage</div>
          <p>{snippet}</p>
        </div>
      )}
      {error && <div className="kb-drawer__error">{error}</div>}
      {view === "chunks" ? (
        <div className="kb-chunks">
          {chunks.length === 0 && <p className="kb-cols__empty">No indexed chunks.</p>}
          {chunks.map((ch) => (
            <div key={ch.chunk_id} className="kb-chunk">
              <div className="kb-chunk__meta">
                <span className="kb-chunk__seq">#{ch.seq}</span>
                <span className="kb-chunk__range">
                  {ch.start}–{ch.end}
                </span>
                <span className={`kb-chunk__cited${ch.cited > 0 ? " is-cited" : ""}`}>
                  <Icon name="quote" size={11} color="currentColor" />
                  {ch.cited} cited
                </span>
              </div>
              <pre className="kb-chunk__text">{ch.text}</pre>
            </div>
          ))}
        </div>
      ) : (
        doc && (
          <article className="md-body">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={rehypePlugins}
              urlTransform={urlTransform}
              components={components}
            >
              {doc.markdown}
            </ReactMarkdown>
          </article>
        )
      )}
    </>
  );
}
