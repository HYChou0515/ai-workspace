/**
 * Shared document renderer used by both the citation drawer (KbDocViewer) and
 * the dedicated page (KbDocPage): fetches a rendered document, shows the cited
 * passage as a highlighted callout, highlights it in place, and turns kb://
 * body links into in-app navigation (the parent decides where — drawer swaps
 * in place, page pushes a route).
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown, { type Components, type Options } from "react-markdown";
import remarkGfm from "remark-gfm";

import { kbApi, type KbApi, type KbRenderedDoc } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { useT } from "../../lib/i18n";
import { parseCsv } from "../../renderers/csv";
import { DataGrid } from "../../renderers/DataGrid";
import { JsonlView } from "../../renderers/JsonlView";
import { JsonTreeView } from "../../renderers/JsonTreeView";
import { baseAwareUrlTransform } from "../../renderers/mdUrlTransform";
import { pickRenderer } from "../../renderers/registry";
import { YamlTree } from "../../renderers/YamlTree";
import { blobHref, parseKbDocHref } from "./kbLinks";
import { rehypeHighlightSnippet } from "./rehypeHighlightSnippet";

// Keep kb:// links intact for the in-app link handler; root-relative URLs the
// BE emits (e.g. `/blobs/...` image siblings) get the deploy sub-path (#73).
const urlTransform = baseAwareUrlTransform("kb://");

// #361: structured-data docs arrive as verbatim text (kb.preview no longer
// projects them to markdown) and render through the SAME pure cores the
// workspace file viewer uses — one implementation, both surfaces. Returns null
// for markdown / text / code, which keep the ReactMarkdown path (and its inline
// snippet highlight). Structured types show the cited-passage callout but no
// inline highlight — a tree/grid can't rehype-highlight a passage (#361 Q11).
function structuredCore(filename: string, text: string): React.ReactNode | null {
  switch (pickRenderer(filename)) {
    case "json":
      return <JsonTreeView text={text} />;
    case "jsonl":
      return <JsonlView text={text} />;
    case "yaml":
      return <YamlTree text={text} />;
    case "csv":
      return <DataGrid rows={parseCsv(text, filename.toLowerCase().endsWith(".tsv") ? "\t" : ",")} />;
    default:
      return null;
  }
}

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
  const t = useT();
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
  // A failed render shows a friendly line, never the raw "render document
  // failed: 404" the API throws — that HTTP status / internal verb is developer
  // noise and used to leak straight to the user (#465).
  const failed = queryError != null;

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
            <Icon name="file" size={13} /> {t("kb.docbody.viewFile")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "chunks"}
            className={`kb-docview-tab${view === "chunks" ? " is-active" : ""}`}
            onClick={() => setView("chunks")}
          >
            <Icon name="layers" size={13} /> {t("kb.docbody.viewChunks", { n: chunks.length })}
          </button>
        </div>
      )}
      {snippet && view === "file" && (
        <div className="kb-docviewer__cited">
          <div className="kb-cites__label">{t("kb.doc.citedPassage")}</div>
          <p>{snippet}</p>
        </div>
      )}
      {failed && <div className="kb-drawer__error">{t("kb.doc.loadError")}</div>}
      {view === "chunks" ? (
        <div className="kb-chunks">
          {chunks.length === 0 && <p className="kb-cols__empty">{t("kb.docbody.noChunks")}</p>}
          {chunks.map((ch) => (
            <div key={ch.chunk_id} className="kb-chunk">
              <div className="kb-chunk__meta">
                <span className="kb-chunk__seq">#{ch.seq}</span>
                <span className="kb-chunk__range">
                  {ch.start}–{ch.end}
                </span>
                <span className={`kb-chunk__cited${ch.cited > 0 ? " is-cited" : ""}`}>
                  <Icon name="quote" size={11} color="currentColor" />
                  {t("kb.docbody.chunkCited", { n: ch.cited })}
                </span>
              </div>
              <pre className="kb-chunk__text">{ch.text}</pre>
            </div>
          ))}
        </div>
      ) : (
        doc &&
        // Issue #39: per-type file view. Browser-native types render the
        // original blob (`<img>` for images; `<iframe>` for PDF — the
        // browser's PDF viewer — and for HTML, sandboxed so uploaded
        // pages can't run scripts). Structured types (json/csv/xlsx/docx)
        // arrive as a markdown projection from the BE (kb.preview) and
        // ride the normal ReactMarkdown path. Anything left with no body
        // (pptx, unknown binary) points at Download; the Chunks tab still
        // shows what got indexed.
        (doc.content_type.startsWith("image/") ? (
          // #114: show the image AND, when the doc was VLM-parsed at ingest,
          // the extracted text the chat actually cited — so the viewer matches
          // what the retriever saw instead of leaving the body blank.
          <>
            <figure className="kb-docimage">
              <img src={blobHref(doc.file_id)} alt={doc.filename} />
            </figure>
            {doc.markdown !== "" && (
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
            )}
          </>
        ) : doc.content_type === "application/pdf" ? (
          <iframe
            className="kb-dociframe"
            src={blobHref(doc.file_id)}
            title={doc.filename}
          />
        ) : doc.content_type === "text/html" ? (
          <iframe
            className="kb-dociframe"
            src={blobHref(doc.file_id)}
            title={doc.filename}
            sandbox=""
          />
        ) : doc.preview_file_id ? (
          // A parser handed back a browser-displayable derivative
          // (pptx → soffice-converted PDF) — render that instead of
          // the original's undisplayable bytes.
          <iframe
            className="kb-dociframe"
            src={blobHref(doc.preview_file_id)}
            title={doc.filename}
          />
        ) : doc.markdown === "" ? (
          <div className="kb-docbinary">
            <Icon name="file" size={14} /> {t("kb.docbody.previewUnavailable")}
          </div>
        ) : (
          (structuredCore(doc.filename, doc.markdown) ?? (
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
          ))
        ))
      )}
    </>
  );
}
