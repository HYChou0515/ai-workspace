/**
 * Shared document renderer used by both the citation drawer (KbDocViewer) and
 * the dedicated page (KbDocPage): fetches a rendered document, shows the cited
 * passage as a highlighted callout, highlights it in place, and turns kb://
 * body links into in-app navigation (the parent decides where — drawer swaps
 * in place, page pushes a route).
 */

import { useEffect, useState } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

import { kbApi, type KbApi, type KbRenderedDoc } from "../../api/kb";
import { parseKbDocHref } from "./kbLinks";
import { rehypeHighlightSnippet } from "./rehypeHighlightSnippet";

export function KbDocBody({
  documentId,
  snippet,
  onNavigate,
  onLoaded,
  client = kbApi,
}: {
  documentId: string;
  snippet?: string;
  /** A kb:// link in the body was followed → the target document id. */
  onNavigate: (targetId: string) => void;
  /** The rendered document finished loading (so chrome can show its name). */
  onLoaded?: (doc: KbRenderedDoc) => void;
  client?: KbApi;
}) {
  const [doc, setDoc] = useState<KbRenderedDoc | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setDoc(null);
    setError(null);
    client
      .renderDocument(documentId)
      .then((d) => {
        if (!mounted) return;
        setDoc(d);
        onLoaded?.(d);
      })
      .catch((e) => mounted && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      mounted = false;
    };
    // onLoaded is intentionally excluded — callers pass inline fns.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId, client]);

  return (
    <>
      {snippet && (
        <div className="kb-docviewer__cited">
          <div className="kb-cites__label">Cited passage</div>
          <p>{snippet}</p>
        </div>
      )}
      {error && <div className="kb-drawer__error">{error}</div>}
      {doc && (
        <article className="md-body">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={snippet ? [[rehypeHighlightSnippet, snippet]] : []}
            // Keep kb:// links intact (default sanitization would drop the
            // unknown scheme); everything else goes through the default.
            urlTransform={(url) => (url.startsWith("kb://") ? url : defaultUrlTransform(url))}
            components={{
              a: ({ href, children }) => {
                const target = href ? parseKbDocHref(href) : null;
                if (target) {
                  return (
                    <button type="button" className="kb-doclink" onClick={() => onNavigate(target)}>
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
            }}
          >
            {doc.markdown}
          </ReactMarkdown>
        </article>
      )}
    </>
  );
}
