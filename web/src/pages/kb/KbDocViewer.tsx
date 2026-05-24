/**
 * KB document viewer — an overlay that renders a source document (markdown
 * from GET /kb/documents/{id}) so a citation can be inspected. kb:// links in
 * the body resolve to in-app navigation to the linked document; the cited
 * passage (when opened from a citation) is shown as a highlighted callout.
 */

import { useEffect, useState } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

import { kbApi, type KbApi, type KbRenderedDoc } from "../../api/kb";
import { Icon } from "../../components/Icon";
import { parseKbDocHref } from "./kbLinks";
import { rehypeHighlightSnippet } from "./rehypeHighlightSnippet";

export function KbDocViewer({
  documentId,
  snippet,
  onClose,
  client = kbApi,
}: {
  documentId: string;
  /** The cited passage text, shown as a callout when opened from a citation. */
  snippet?: string;
  onClose: () => void;
  client?: KbApi;
}) {
  const [docId, setDocId] = useState(documentId);
  const [doc, setDoc] = useState<KbRenderedDoc | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setDocId(documentId), [documentId]);

  useEffect(() => {
    let mounted = true;
    setDoc(null);
    setError(null);
    client
      .renderDocument(docId)
      .then((d) => mounted && setDoc(d))
      .catch((e) => mounted && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      mounted = false;
    };
  }, [docId, client]);

  return (
    <>
      <div className="kb-drawer-backdrop" onClick={onClose} aria-hidden />
      <aside className="kb-docviewer" role="dialog" aria-label="Document">
        <header className="kb-docviewer__head">
          <Icon name="file" size={16} color="var(--text-paper-d)" />
          <span className="kb-docviewer__name">{doc?.filename ?? docId.split("/").pop()}</span>
          <button type="button" className="kb-iconbtn" aria-label="Close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </header>
        <div className="kb-docviewer__body">
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
                        <button
                          type="button"
                          className="kb-doclink"
                          onClick={() => setDocId(target)}
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
                }}
              >
                {doc.markdown}
              </ReactMarkdown>
            </article>
          )}
        </div>
      </aside>
    </>
  );
}
