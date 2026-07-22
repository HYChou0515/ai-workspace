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

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { FileServiceProvider } from "../../api/fileService";
import { kbApi, type KbApi, type KbDocument } from "../../api/kb";
import { kbFileService, normPath } from "../../api/kbFileService";
import { qk } from "../../api/queryKeys";
import { DialogProvider, useDialog } from "../../components/Dialog";
import { Icon } from "../../components/Icon";
import { PermissionDialog } from "../../components/PermissionDialog";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { useIsSuperuser } from "../../hooks/useIsSuperuser";
import { DOC_ROLES, type CollectionPermission, canManageAccess } from "../../lib/permission";
import { ResizeDivider } from "../../components/ResizeDivider";
import { EditModeProvider, useEditMode } from "../../hooks/editMode";
import { usePersistentNumber } from "../../hooks/usePersistentNumber";
import {
  FileBufferProvider,
  FileBufferStore,
  type IO,
  reactQueryContentCache,
  useFileBuffer,
  useIsDirty,
} from "../../hooks/fileBuffer";
import { useT } from "../../lib/i18n";
import { FileView } from "../../renderers/FileView";
import { hasEditToggle, pickRenderer } from "../../renderers/registry";
import { FileTree } from "../investigation/FileTree";
import { AttachmentBar } from "./AttachmentBar";
import { KbDocViewer } from "./KbDocViewer";
import { TuneParsingModal } from "./TuneParsingModal";
import { docHref } from "./kbLinks";
import { QualityBadge } from "./QualityBadge";
import { QualityDetails } from "./QualityDetails";
import { decodeLeafPath, encodeLeafPath } from "./leafPath";
import { pxToRem } from "../../lib/pxToRem";
import { useCollectionDocs } from "./useCollectionDocs";

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
        borderRadius: "var(--radius-card)",
        color: "var(--text-paper-d)",
        textAlign: "center",
      }}
    >
      <Icon name="upload" size={28} color="var(--text-paper-d2)" />
      <div style={{ fontSize: pxToRem(14) }}>{t("kb.dropHint")}</div>
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

type KbDocIdeProps = {
  collectionId: string;
  client?: KbApi;
  // #172: when the page wires these in, an empty collection shows an upload
  // call-to-action (drop hint + button) instead of passive text.
  onPickFiles?: () => void;
  uploading?: boolean;
};

// The DialogProvider wraps the whole body (not just the file tree) so the bulk
// re-index confirm — raised from `reindexPaths`, which resolves a tree
// selection to its actual doc set — can reach the shared modal.
export function KbDocIde(props: KbDocIdeProps) {
  return (
    <DialogProvider>
      <KbDocIdeBody {...props} />
    </DialogProvider>
  );
}

function KbDocIdeBody({
  collectionId,
  client = kbApi,
  onPickFiles,
  uploading = false,
}: KbDocIdeProps) {
  const qc = useQueryClient();
  const dialog = useDialog();
  // #395: one-request fetch-all + a cheap status poll while anything indexes
  // (progress merges in client-side; the list refetches only on real change).
  const { docs, docsQuery } = useCollectionDocs(collectionId, client);
  // #328/#356: the doc whose Tune-parsing modal is open (null = closed).
  const [tuneDoc, setTuneDoc] = useState<KbDocument | null>(null);
  const refetch = useCallback(() => {
    void qc.invalidateQueries({ queryKey: qk.kb.documents(collectionId) });
    // A mutation (upload / move / reindex) can start an indexing wave — nudge
    // the summary too so its poll gate reopens without waiting for the list.
    void qc.invalidateQueries({ queryKey: qk.kb.documentsStatus(collectionId) });
  }, [qc, collectionId]);

  // #308: the per-doc "Permissions" action is offered to whoever the backend
  // authority (_authorize_doc_permission) accepts: the COLLECTION owner or a
  // superuser. Owner-ness is a string compare against the cached collection
  // list; the endpoints re-check server-side.
  const me = useCurrentUser();
  const isSuperuser = useIsSuperuser();
  const collectionsQuery = useQuery({
    queryKey: qk.kb.collections,
    queryFn: () => client.listCollections(),
  });
  const collectionOwner = collectionsQuery.data?.find(
    (c) => c.resource_id === collectionId,
  )?.owner;
  const canManagePerms = canManageAccess(collectionOwner, me, isSuperuser);
  // The doc whose per-doc read-override dialog is open (null = closed) — the same
  // shape as `tuneDoc`, opened from the editor header.
  const [permDoc, setPermDoc] = useState<KbDocument | null>(null);
  const permQuery = useQuery({
    queryKey: qk.kb.docPermission(permDoc?.resource_id ?? "__none__"),
    enabled: permDoc != null,
    queryFn: () => client.getDocPermission((permDoc as KbDocument).resource_id),
  });
  const setPermMut = useMutation({
    mutationFn: (perm: CollectionPermission) =>
      client.setDocPermission((permDoc as KbDocument).resource_id, perm),
    onSuccess: () => {
      refetch(); // visibility may now hide the doc from some viewers
      setPermDoc(null);
    },
  });
  const clearPermMut = useMutation({
    mutationFn: () => client.clearDocPermission((permDoc as KbDocument).resource_id),
    onSuccess: () => {
      refetch();
      setPermDoc(null);
    },
  });

  const service = useMemo(
    () => kbFileService(collectionId, docs, client, refetch),
    [collectionId, docs, client, refetch],
  );
  // The buffer store stays stable across status polls (which rebuild `service`)
  // by reading the LATEST service through a ref — so an open file's edits are
  // never reset by a background refetch.
  const serviceRef = useRef(service);
  serviceRef.current = service;
  const bufferStore = useMemo(() => {
    // One io the store AND its content cache share, so the buffer's fetch and
    // any other qk.file reader dedupe onto the SAME cache entry (scopeId is
    // stable per collection, so reading it off the ref once is fine).
    const io: IO = {
      readFile: (p) => serviceRef.current.readFile(p),
      writeFile: (p, b) => serviceRef.current.writeFile(p, b),
    };
    return new FileBufferStore(io, reactQueryContentCache(qc, serviceRef.current.scopeId, io));
  }, [qc]);

  const navigate = useNavigate();
  const params = useParams();
  // The open document is the URL (#93): /kb/collections/:cid/documents/<path>.
  const urlPath = params["*"] ? decodeLeafPath(params["*"]) : null;
  // Key everything by the canonical (leading-slash) path so a doc stored
  // relative ("mydir/x.md") and the tree's path ("/mydir/x.md") line up —
  // otherwise an inferred folder never matches its files (#87).
  // #513 P8: attachments (parent_doc_id set) live under the reserved `.att/`
  // namespace and are shown as a card list under their parent — NOT in the path
  // tree. Everything tree-facing (the file list, inferred folders, the path→doc
  // map that gates the editor) is derived from the top-level docs only. The full
  // `docs` still backs the file service + the per-parent attachment lookup.
  const treeDocs = useMemo(() => docs.filter((d) => !d.parent_doc_id), [docs]);
  const docByPath = useMemo(() => new Map(treeDocs.map((d) => [normPath(d.path), d])), [treeDocs]);
  // Only treat the URL's doc as "open" once it's really in the list. A just-
  // uploaded / freshly-moved doc lands in the URL before the refetch brings it
  // in, and reading its bytes early throws "unknown KB document"; until then we
  // show the empty pane and let the poll/refetch promote it.
  const activePath = urlPath && docByPath.has(urlPath) ? urlPath : null;
  // The open doc's rationale (status bar) + parser-guidance override (Tune modal)
  // no longer ride the (metas-only) list rows. Fetch JUST those two fields from
  // the SourceDoc envelope — a cheap metadata point-get — NOT renderDocument,
  // which re-reads the content blob and runs count queries to project a markdown
  // body this IDE discards (that heavy path stays for the citation drawer).
  const activeDoc = activePath ? docByPath.get(activePath) : undefined;
  const docMetaQuery = useQuery({
    queryKey: qk.kb.docMeta(activeDoc?.resource_id ?? "__none__"),
    enabled: activeDoc != null,
    queryFn: () => client.getSourceDocMeta((activeDoc as KbDocument).resource_id),
  });
  // `.gitkeep` is the hidden placeholder that keeps an otherwise-empty folder
  // alive — drop it from the file list, but surface its directory so the empty
  // folder still shows in the tree.
  const files = useMemo(
    () =>
      treeDocs
        .filter((d) => !d.path.endsWith("/.gitkeep") && d.path !== ".gitkeep")
        .map((d) => ({ path: normPath(d.path), size: d.size ?? 0 })),
    [treeDocs],
  );
  const dirs = useMemo(() => {
    const out = new Set<string>();
    for (const d of treeDocs) {
      if (d.path.endsWith("/.gitkeep")) out.add(normPath(d.path.slice(0, -"/.gitkeep".length)));
    }
    return [...out];
  }, [treeDocs]);

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

  // #513 P8: the open doc's attachments (child docs under its `.att/` namespace)
  // + the drawer that shows one. `viewerId` is the attachment being inspected in
  // the shared KbDocViewer — the SAME drawer a citation opens, so images / PDFs /
  // CSVs all render with zero per-type code.
  const [viewerId, setViewerId] = useState<string | null>(null);
  const attachments = useMemo(
    () => (activeDoc ? docs.filter((d) => d.parent_doc_id === activeDoc.resource_id) : []),
    [docs, activeDoc],
  );
  // Add an attachment: an ordinary upload to `{parent}/.att/{filename}` — the BE
  // links it to the parent by that reserved-namespace path (no special param).
  const attUpload = useCallback(
    async (parentPath: string, file: File) => {
      await client.uploadDocument(collectionId, file, `${parentPath}/.att/${file.name}`);
      refetch();
    },
    [client, collectionId, refetch],
  );
  // Replace an attachment's bytes: upload to its OWN path (last-write-wins in
  // place, then re-index) — same content-addressed upsert as any re-upload.
  const attReplace = useCallback(
    async (a: KbDocument, file: File) => {
      await client.uploadDocument(collectionId, file, a.path);
      refetch();
    },
    [client, collectionId, refetch],
  );
  const attDelete = useCallback(
    async (a: KbDocument) => {
      await client.deleteDocument(a.resource_id);
      setViewerId((cur) => (cur === a.resource_id ? null : cur));
      refetch();
    },
    [client, refetch],
  );
  // Rename an attachment: swap the basename, keeping its `{parent}/.att/…` dir —
  // the ordinary `move` (a path change re-keys). A name clash is the BE's 409,
  // surfaced verbatim rather than silently disambiguated.
  const attRename = useCallback(
    async (a: KbDocument, newName: string) => {
      const dir = a.path.slice(0, a.path.lastIndexOf("/"));
      try {
        await client.moveDocument(a.resource_id, `${dir}/${newName}`);
        refetch();
      } catch (e) {
        await dialog.confirm({
          title: "Couldn't rename attachment",
          body: e instanceof Error ? e.message : "That name is already taken.",
          actions: [{ id: "ok", label: "OK", variant: "primary" }],
        });
      }
    },
    [client, refetch, dialog],
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
      if (ids.size === 0) return;
      // Re-indexing >=2 documents at once restarts a lot of work — confirm first.
      // A single doc (right-click one file) reindexes straight away. The count is
      // the resolved doc set, so a folder that expands to many docs also confirms.
      if (ids.size >= 2) {
        const choice = await dialog.confirm({
          title: `Re-read ${ids.size} documents`,
          body: `Re-read all ${ids.size} selected documents? The AI reads each one again from scratch.`,
          actions: [
            { id: "go", label: "Re-read", variant: "primary" },
            { id: "cancel", label: "Cancel" },
          ],
        });
        if (choice !== "go") return;
      }
      for (const id of ids) await client.reindexDocument(id);
      refetch();
    },
    [docs, docByPath, client, refetch, dialog],
  );
  // #402: draggable tree width, persisted + clamped. Shared key with the wiki
  // IDE so the two KB trees remember one width. `treeStart` snapshots the width
  // at drag start; ResizeDivider reports the signed delta from there.
  const [treeW, setTreeW] = usePersistentNumber("kb:ide:treeWidth", 260, 160, 560);
  const treeStart = useRef(treeW);
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
          <div className="kb-ide">
              <div className="kb-ide__main">
                <div className="kb-ide__tree" style={{ width: treeW, flexShrink: 0 }}>
                  <FileTree
                    files={files}
                    dirs={dirs}
                    activePath={activePath}
                    onOpen={openPath}
                    onChanged={refetch}
                    onReindex={(paths) => void reindexPaths(paths)}
                    searchable
                    decorate={(path) => (
                      <>
                        <QualityBadge score={docByPath.get(path)?.quality_score} />
                        <KbRowBadge path={path} status={docByPath.get(path)?.status} />
                      </>
                    )}
                  />
                </div>
                <ResizeDivider
                  orientation="vertical"
                  ariaLabel="Resize file tree"
                  onResizeStart={() => {
                    treeStart.current = treeW;
                  }}
                  onResize={(d) => setTreeW(treeStart.current + d)}
                />
                <div className="kb-ide__pane">
                  {activePath ? (
                    <KbEditorPane
                      path={activePath}
                      doc={docByPath.get(activePath)}
                      onReindex={reindex}
                      onTune={setTuneDoc}
                      onPermissions={canManagePerms ? setPermDoc : undefined}
                      attachments={attachments}
                      onOpenAttachment={setViewerId}
                      onUploadAttachment={attUpload}
                      onReplaceAttachment={attReplace}
                      onDeleteAttachment={attDelete}
                      onRenameAttachment={attRename}
                    />
                  ) : (
                    <div className="kb-ide__empty">Select a document to view or edit.</div>
                  )}
                </div>
              </div>
              <KbStatusBar
                doc={activeDoc}
                rationale={docMetaQuery.data?.quality_rationale}
                breakdown={docMetaQuery.data?.quality_breakdown}
              />
              {tuneDoc && (
                <TuneParsingModal
                  collectionId={collectionId}
                  docId={tuneDoc.resource_id}
                  docPath={tuneDoc.path}
                  // Tune opens from the editor header, so the tuned doc IS the
                  // open one — its doc-meta fetch (above) carries the override.
                  docGuidance={
                    tuneDoc.resource_id === activeDoc?.resource_id
                      ? docMetaQuery.data?.parser_guidance_override
                      : undefined
                  }
                  onClose={() => setTuneDoc(null)}
                  client={client}
                />
              )}
              {permDoc && permQuery.data && collectionOwner && (
                <PermissionDialog
                  resourceName={permDoc.path}
                  owner={collectionOwner}
                  value={permQuery.data}
                  roles={DOC_ROLES}
                  caption="Choose who can read this document. It can only restrict access further than the collection — never widen it."
                  busy={setPermMut.isPending || clearPermMut.isPending}
                  onSubmit={(perm) => {
                    // "Public" ⇒ no restriction ⇒ remove the override (revert to
                    // pure collection inheritance); otherwise store the tightening.
                    if (perm.visibility === "public") clearPermMut.mutate();
                    else setPermMut.mutate(perm);
                  }}
                  onClose={() => setPermDoc(null)}
                />
              )}
              {viewerId && (
                // #513 P8: inspect an attachment in the SAME drawer a citation
                // opens — it renders any SourceDoc (image / PDF / CSV / …) and
                // carries its own download / re-read / delete actions.
                <KbDocViewer
                  documentId={viewerId}
                  onClose={() => setViewerId(null)}
                  onChanged={refetch}
                  client={client}
                />
              )}
          </div>
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
 * per-doc chunks / cited / size that used to live in the table column.
 * `rationale` (#105 "why good/bad") arrives from the open doc's render call —
 * #395 moved it off the list row. */
function KbStatusBar({
  doc,
  rationale,
  breakdown,
}: {
  doc?: KbDocument;
  rationale?: string;
  breakdown?: Record<string, number>;
}) {
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
      {doc.status === "indexing" && (doc.units_total ?? 0) > 0 ? (
        // #248: a REAL done/total bar from the fan-out aggregate — only ever
        // climbs, unlike the old per-page status_detail string that N parallel
        // batches clobbered (making it jump backward).
        <span className="kb-ide__status-progress mono" data-testid="kb-index-progress">
          <progress
            className="kb-ide__status-bar"
            value={doc.units_done ?? 0}
            max={doc.units_total}
          />
          {doc.units_done ?? 0} / {doc.units_total}
        </span>
      ) : (
        doc.status_detail && (
          <span className="kb-ide__status-detail" title={doc.status_detail}>
            {doc.status_detail}
          </span>
        )
      )}
      <span className="kb-ide__status-state">{status}</span>
      {typeof doc.quality_score === "number" && (
        // #105 / #460 P7+P8: the AI quality verdict — coloured grade + visible
        // good/ok/bad label, expanding into the full rationale + per-dimension
        // breakdown. Only when the doc has been judged.
        <QualityDetails score={doc.quality_score} rationale={rationale} breakdown={breakdown} />
      )}
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
  onTune,
  onPermissions,
  attachments,
  onOpenAttachment,
  onUploadAttachment,
  onReplaceAttachment,
  onDeleteAttachment,
  onRenameAttachment,
}: {
  path: string;
  doc?: KbDocument;
  onReindex?: (docId: string) => void;
  onTune?: (doc: KbDocument) => void;
  /** #308: open the per-doc read-override dialog (collection owner only; the
   * parent passes `undefined` for everyone else). */
  onPermissions?: (doc: KbDocument) => void;
  /** #513 P8: this doc's attachments + their CRUD, shown below the content. */
  attachments: KbDocument[];
  onOpenAttachment: (documentId: string) => void;
  onUploadAttachment: (parentPath: string, file: File) => void;
  onReplaceAttachment: (att: KbDocument, file: File) => void;
  onDeleteAttachment: (att: KbDocument) => void;
  onRenameAttachment: (att: KbDocument, newName: string) => void;
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
            title="Have the AI re-read this document — updates search and answers"
            disabled={doc.status === "indexing"}
            onClick={() => onReindex(doc.resource_id)}
          >
            Re-read
          </button>
        )}
        {doc && onTune && (
          // #356: open the Tune-parsing modal for this document — edit the parse
          // prompt (per-doc or collection), preview the re-parse, and try answering.
          <button
            type="button"
            className="kb-btn"
            title={t("kb.tuneParsing.buttonTitle")}
            onClick={() => onTune(doc)}
          >
            {t("kb.tuneParsing.button")}
          </button>
        )}
        {doc && onPermissions && (
          // #308: open the per-doc read-override dialog (collection owner only).
          <button
            type="button"
            className="kb-btn"
            data-testid="doc-permissions"
            title="Restrict who can read this document"
            onClick={() => onPermissions(doc)}
          >
            <Icon name="users" size={13} /> Permissions
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
        <AttachmentBar
          parentPath={path}
          attachments={attachments}
          onOpen={onOpenAttachment}
          onUpload={(f) => onUploadAttachment(path, f)}
          onReplace={onReplaceAttachment}
          onDelete={onDeleteAttachment}
          onRename={onRenameAttachment}
        />
      </div>
    </div>
  );
}
