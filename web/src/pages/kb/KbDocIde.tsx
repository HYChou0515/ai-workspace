/**
 * KbDocIde — a KB collection's documents as a VSCode-shaped tree + editor,
 * built entirely from the shared investigation IDE pieces (#87): the same
 * FileTree, FileView/renderers, file-buffer + edit-mode, driven by a
 * `kbFileService` instead of the investigation file API.
 *
 * Read/edit/save round-trips on the raw document bytes via specstar auto-CRUD
 * (see kbFileService); a save re-uploads the blob + CAS-PATCHes the content
 * reference, and the SourceDoc patch handler (P2) re-indexes. v1 is a flat
 * path space — the tree hides new-file / folder / rename / drag via the
 * service caps; new docs still arrive through the upload button.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { FileServiceProvider } from "../../api/fileService";
import { kbApi, type KbApi, type KbDocument } from "../../api/kb";
import { kbFileService, normPath } from "../../api/kbFileService";
import { qk } from "../../api/queryKeys";
import { DialogProvider } from "../../components/Dialog";
import { Icon } from "../../components/Icon";
import { EditModeProvider, useEditMode } from "../../hooks/editMode";
import {
  FileBufferProvider,
  FileBufferStore,
  useFileBuffer,
  useIsDirty,
} from "../../hooks/fileBuffer";
import { useT } from "../../lib/i18n";
import { FileView } from "../../renderers/FileView";
import { hasEditToggle, pickRenderer } from "../../renderers/registry";
import { FileTree } from "../investigation/FileTree";
import { docHref } from "./kbLinks";
import { decodeLeafPath, encodeLeafPath } from "./leafPath";

/** Page through the (paged) documents endpoint into one flat list — the tree
 * needs every path, not a slice. `collection_id` is indexed on the BE, so this
 * is cheap even for a large collection. Exported so the collection page can
 * share the SAME query (key + fetcher) for its index-status strip (#162). */
export async function fetchAllDocs(
  client: Pick<KbApi, "listDocuments">,
  collectionId: string,
): Promise<KbDocument[]> {
  const out: KbDocument[] = [];
  const limit = 200;
  for (let offset = 0; ; offset += limit) {
    const page = await client.listDocuments(collectionId, { offset, limit });
    out.push(...page.items);
    if (!page.has_more || page.items.length === 0) break;
  }
  return out;
}

/** The empty-collection greeting (#172): a drop-zone-styled call to action so
 * uploading is obvious from the first screen. The actual drop is handled by the
 * page-level overlay that wraps this pane; the button opens the file picker. */
function EmptyUploadCta({
  onPickFiles,
  uploading,
}: {
  onPickFiles: () => void;
  uploading: boolean;
}) {
  const t = useT();
  return (
    <div
      data-testid="kb-docs-empty-cta"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        padding: 40,
        margin: 12,
        border: "2px dashed var(--paper-3)",
        borderRadius: 10,
        color: "var(--text-paper-d)",
        textAlign: "center",
      }}
    >
      <Icon name="upload" size={28} color="var(--text-paper-d2)" />
      <div style={{ fontSize: 14 }}>{t("kb.dropHint")}</div>
      <button
        type="button"
        className="kb-btn kb-btn--primary"
        disabled={uploading}
        onClick={onPickFiles}
      >
        <Icon name="file" size={13} /> {t("kb.uploadFiles")}
      </button>
    </div>
  );
}

export function KbDocIde({
  collectionId,
  client = kbApi,
  onPickFiles,
  uploading = false,
}: {
  collectionId: string;
  client?: KbApi;
  // #172: when the page wires these in, an empty collection shows an upload
  // call-to-action (drop hint + button) instead of passive text.
  onPickFiles?: () => void;
  uploading?: boolean;
}) {
  const qc = useQueryClient();
  const docsQuery = useQuery({
    queryKey: qk.kb.documents(collectionId),
    queryFn: () => fetchAllDocs(client, collectionId),
    // Poll while anything is indexing so the tree badge + status clear live.
    refetchInterval: (q) =>
      (q.state.data as KbDocument[] | undefined)?.some((d) => d.status === "indexing")
        ? 1500
        : false,
  });
  const docs = useMemo(() => docsQuery.data ?? [], [docsQuery.data]);
  const refetch = useCallback(() => {
    void qc.invalidateQueries({ queryKey: qk.kb.documents(collectionId) });
  }, [qc, collectionId]);

  const service = useMemo(
    () => kbFileService(collectionId, docs, client, refetch),
    [collectionId, docs, client, refetch],
  );
  // The buffer store stays stable across status polls (which rebuild `service`)
  // by reading the LATEST service through a ref — so an open file's edits are
  // never reset by a background refetch.
  const serviceRef = useRef(service);
  serviceRef.current = service;
  const bufferStore = useMemo(
    () =>
      new FileBufferStore({
        readFile: (p) => serviceRef.current.readFile(p),
        writeFile: (p, b) => serviceRef.current.writeFile(p, b),
      }),
    [],
  );

  const navigate = useNavigate();
  const params = useParams();
  // The open document is the URL (#93): /kb/collections/:cid/documents/<path>.
  const urlPath = params["*"] ? decodeLeafPath(params["*"]) : null;
  // Key everything by the canonical (leading-slash) path so a doc stored
  // relative ("mydir/x.md") and the tree's path ("/mydir/x.md") line up —
  // otherwise an inferred folder never matches its files (#87).
  const docByPath = useMemo(() => new Map(docs.map((d) => [normPath(d.path), d])), [docs]);
  // Only treat the URL's doc as "open" once it's really in the list. A just-
  // uploaded / freshly-moved doc lands in the URL before the refetch brings it
  // in, and reading its bytes early throws "unknown KB document"; until then we
  // show the empty pane and let the poll/refetch promote it.
  const activePath = urlPath && docByPath.has(urlPath) ? urlPath : null;
  // `.gitkeep` is the hidden placeholder that keeps an otherwise-empty folder
  // alive — drop it from the file list, but surface its directory so the empty
  // folder still shows in the tree.
  const files = useMemo(
    () =>
      docs
        .filter((d) => !d.path.endsWith("/.gitkeep") && d.path !== ".gitkeep")
        .map((d) => ({ path: normPath(d.path), size: d.size ?? 0 })),
    [docs],
  );
  const dirs = useMemo(() => {
    const out = new Set<string>();
    for (const d of docs) {
      if (d.path.endsWith("/.gitkeep")) out.add(normPath(d.path.slice(0, -"/.gitkeep".length)));
    }
    return [...out];
  }, [docs]);

  // Opening a tree node routes to the doc's URL; the splat above is the single
  // source of truth for which doc is open (covers create / upload / move — the
  // editor just waits for the doc to land in the list, see `activePath`).
  const docsBase = `/kb/collections/${encodeURIComponent(collectionId)}/documents`;
  const openPath = useCallback(
    (p: string) => navigate(`${docsBase}/${encodeLeafPath(p)}`),
    [navigate, docsBase],
  );
  // Re-chunk + re-embed a doc on demand (e.g. after an embedder fix) without
  // editing it — the per-doc action the old documents table had.
  const reindex = useCallback(
    async (docId: string) => {
      await client.reindexDocument(docId);
      refetch();
    },
    [client, refetch],
  );
  // Reindex a tree selection (#98): resolve each path to its doc — a folder
  // expands to every descendant doc — then re-index each once.
  const reindexPaths = useCallback(
    async (paths: string[]) => {
      const ids = new Set<string>();
      for (const p of paths) {
        const np = normPath(p);
        const exact = docByPath.get(np);
        if (exact) ids.add(exact.resource_id);
        else for (const d of docs) if (normPath(d.path).startsWith(np + "/")) ids.add(d.resource_id);
      }
      for (const id of ids) await client.reindexDocument(id);
      refetch();
    },
    [docs, docByPath, client, refetch],
  );
  if (docsQuery.isPending) {
    return (
      <p className="kb-cols__empty" role="status" aria-live="polite">
        Loading documents…
      </p>
    );
  }
  if (docs.length === 0) {
    // With the page's picker wired in, greet the empty collection with a real
    // drop-zone CTA (#172); otherwise fall back to the plain hint.
    return onPickFiles ? (
      <EmptyUploadCta onPickFiles={onPickFiles} uploading={uploading} />
    ) : (
      <p className="kb-cols__empty">Upload markdown, text, or an archive to index it.</p>
    );
  }

  return (
    <FileServiceProvider value={service}>
      <FileBufferProvider store={bufferStore}>
        <EditModeProvider>
          <DialogProvider>
            <div className="kb-ide">
              <div className="kb-ide__main">
                <div className="kb-ide__tree">
                  <FileTree
                    files={files}
                    dirs={dirs}
                    activePath={activePath}
                    onOpen={openPath}
                    onChanged={refetch}
                    onReindex={(paths) => void reindexPaths(paths)}
                    decorate={(path) => (
                      <KbRowBadge path={path} status={docByPath.get(path)?.status} />
                    )}
                  />
                </div>
                <div className="kb-ide__pane">
                  {activePath ? (
                    <KbEditorPane
                      path={activePath}
                      doc={docByPath.get(activePath)}
                      onReindex={reindex}
                    />
                  ) : (
                    <div className="kb-ide__empty">Select a document to view or edit.</div>
                  )}
                </div>
              </div>
              <KbStatusBar doc={activePath ? docByPath.get(activePath) : undefined} />
            </div>
          </DialogProvider>
        </EditModeProvider>
      </FileBufferProvider>
    </FileServiceProvider>
  );
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** VSCode-style bottom status bar: the open doc's path + index status, plus the
 * per-doc chunks / cited / size that used to live in the table column. */
function KbStatusBar({ doc }: { doc?: KbDocument }) {
  const t = useT();
  if (!doc) {
    return <div className="kb-ide__status kb-ide__status--empty" data-testid="kb-ide-status" />;
  }
  const status =
    doc.status === "ready"
      ? t("kb.doc.ready")
      : doc.status === "indexing"
        ? t("kb.doc.processing")
        : t("kb.doc.failed");
  // The chunk count doubles as the entry point to the full chunks view — the
  // doc IDE has no inline chunks panel, so clicking it opens the dedicated
  // page (File ⇄ Chunks) in a new tab, keeping the editor where it is.
  const chunksLabel =
    typeof doc.chunks === "number" ? `${doc.chunks} chunk${doc.chunks === 1 ? "" : "s"}` : null;
  const rest = [
    typeof doc.cited === "number" && doc.cited > 0 ? `cited ${doc.cited}×` : null,
    typeof doc.size === "number" ? fmtBytes(doc.size) : null,
  ].filter(Boolean);
  return (
    <div className={`kb-ide__status kb-ide__status--${doc.status}`} data-testid="kb-ide-status">
      <span className="kb-ide__status-path mono">{doc.path}</span>
      <span className="kb-ide__status-spacer" />
      {doc.status_detail && (
        <span className="kb-ide__status-detail" title={doc.status_detail}>
          {doc.status_detail}
        </span>
      )}
      <span className="kb-ide__status-state">{status}</span>
      {(chunksLabel || rest.length > 0) && (
        <span className="kb-ide__status-meta">
          {chunksLabel && (
            <a
              className="kb-ide__status-chunks"
              href={docHref(doc.resource_id)}
              target="_blank"
              rel="noreferrer"
              title="View indexed chunks"
            >
              {chunksLabel}
            </a>
          )}
          {chunksLabel && rest.length > 0 ? " · " : ""}
          {rest.join(" · ")}
        </span>
      )}
    </div>
  );
}

/** Trailing tree-row badge: an unsaved dot wins, else the doc's index status. */
function KbRowBadge({ path, status }: { path: string; status?: string }) {
  const t = useT();
  const dirty = useIsDirty(path);
  if (dirty) {
    return (
      <span className="kb-ide__dot" title="Unsaved changes" aria-label="unsaved">
        ●
      </span>
    );
  }
  if (status === "indexing") {
    return (
      <span className="kb-ide__badge" title={t("kb.doc.processing")}>
        ⟳
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="kb-ide__badge kb-ide__badge--err" title={t("kb.doc.processingFailed")}>
        !
      </span>
    );
  }
  return null;
}

function KbEditorPane({
  path,
  doc,
  onReindex,
}: {
  path: string;
  doc?: KbDocument;
  onReindex?: (docId: string) => void;
}) {
  const t = useT();
  const { save } = useFileBuffer(path);
  const dirty = useIsDirty(path);
  const { isEditing, toggle } = useEditMode();
  const canToggle = hasEditToggle(pickRenderer(path));
  const editing = isEditing(path);

  // ⌘S / Ctrl-S saves the active file (the renderers also edit through the
  // shared buffer, so this is the one save path).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  return (
    <div className="kb-ide__editor">
      <header className="kb-ide__bar">
        <span className="kb-ide__crumb mono">{path}</span>
        {doc && doc.status !== "ready" && (
          <span className={`kb-status kb-status--${doc.status}`}>
            {doc.status === "indexing" ? t("kb.doc.processing") : t("kb.doc.failed")}
          </span>
        )}
        <span className="kb-ide__spacer" />
        {doc && onReindex && (
          <button
            type="button"
            className="kb-btn"
            title="Re-chunk + re-embed this document"
            disabled={doc.status === "indexing"}
            onClick={() => onReindex(doc.resource_id)}
          >
            Reindex
          </button>
        )}
        {canToggle && (
          <button type="button" className="kb-btn" onClick={() => toggle(path)}>
            {editing ? "Preview" : "Edit"}
          </button>
        )}
        <button
          type="button"
          className="kb-btn kb-btn--primary"
          disabled={!dirty}
          onClick={() => void save()}
        >
          {dirty ? "Save" : "Saved"}
        </button>
      </header>
      <div className="kb-ide__body">
        <FileView path={path} />
      </div>
    </div>
  );
}
