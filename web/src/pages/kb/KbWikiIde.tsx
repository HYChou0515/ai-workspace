/**
 * KbWikiIde — a collection's LLM wiki as a VSCode-shaped tree + editor (#D),
 * built from the same shared pieces as the doc IDE: the FileTree shell over a
 * `wikiFileService`, plus a preview pane that keeps the wiki's reading
 * experience ([[wikilink]] navigation + a clickable Sources footer).
 *
 * The wiki stays AI-maintained — this just lets a human fix / add / move pages.
 * Saves re-write the page in place (shared drive, last write wins; the
 * maintainer may later revise it); the wiki has no upload (the tree hides it).
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { FileServiceProvider } from "../../api/fileService";
import { kbApi, type KbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { normPath } from "../../api/kbFileService";
import { wikiFileService } from "../../api/wikiFileService";
import { MonacoEditor } from "../../components/MonacoEditor";
import { DialogProvider } from "../../components/Dialog";
import { ResizeDivider } from "../../components/ResizeDivider";
import { EditModeProvider, useEditMode } from "../../hooks/editMode";
import { FileBufferProvider, FileBufferStore, useFileBuffer, useIsDirty } from "../../hooks/fileBuffer";
import { usePersistentNumber } from "../../hooks/usePersistentNumber";
import { FileTree } from "../investigation/FileTree";
import { decodeLeafPath, encodeLeafPath } from "./leafPath";
import { stem, WikiPageBody } from "./WikiPageBody";

export function KbWikiIde({
  collectionId,
  onOpenDoc,
  client = kbApi,
}: {
  collectionId: string;
  /** Open one of the collection's source documents (a Sources footer click). */
  onOpenDoc?: (documentId: string) => void;
  client?: KbApi;
}) {
  const qc = useQueryClient();
  const pagesQuery = useQuery({
    queryKey: qk.kb.wikiPages(collectionId),
    queryFn: () => client.listWikiPages(collectionId),
  });
  const allPages = useMemo(() => pagesQuery.data?.pages ?? [], [pagesQuery.data]);
  const refetch = useCallback(() => {
    void qc.invalidateQueries({ queryKey: qk.kb.wikiPages(collectionId) });
  }, [qc, collectionId]);

  const service = useMemo(
    () => wikiFileService(collectionId, allPages, client, refetch),
    [collectionId, allPages, client, refetch],
  );
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

  // Source path → document id, for the Sources footer.
  const { data: docsPage } = useQuery({
    queryKey: qk.kb.documentsPage(collectionId, 0, 500),
    queryFn: () => client.listDocuments(collectionId, { offset: 0, limit: 500 }),
  });
  const docIdByPath = useMemo(() => {
    const m = new Map<string, string>();
    for (const d of docsPage?.items ?? []) {
      m.set(d.path, d.resource_id);
      m.set(d.path.split("/").pop() ?? d.path, d.resource_id); // basename fallback
    }
    return m;
  }, [docsPage]);

  // `.gitkeep` keeps an otherwise-empty folder alive — drop it from the tree,
  // but surface its directory so the empty folder still shows.
  const visiblePages = useMemo(() => allPages.filter((p) => !p.endsWith(".gitkeep")), [allPages]);
  const files = useMemo(() => visiblePages.map((p) => ({ path: normPath(p), size: 0 })), [visiblePages]);
  const dirs = useMemo(() => {
    const out = new Set<string>();
    for (const p of allPages) {
      if (p.endsWith("/.gitkeep")) out.add(normPath(p.slice(0, -"/.gitkeep".length)));
    }
    return [...out];
  }, [allPages]);
  const filePaths = useMemo(() => files.map((f) => f.path), [files]);
  const pageSet = useMemo(() => new Set(filePaths), [filePaths]);

  const navigate = useNavigate();
  const params = useParams();
  // The open page is the URL (#93): /kb/collections/:cid/wiki/<path>. With no
  // page in the URL we show index/first WITHOUT navigating, so the wiki landing
  // stays at /wiki. A just-created / moved page lands in the URL before the
  // refetch brings it in, so the editor only mounts once it's really in the
  // list (`pageSet`) — otherwise we'd read a page that isn't there yet.
  const fromUrl = params["*"] ? decodeLeafPath(params["*"]) : null;
  const fallback = visiblePages.find((p) => p.endsWith("/index.md")) ?? visiblePages[0];
  const target = fromUrl ?? (fallback ? normPath(fallback) : null);
  const activePath = target && pageSet.has(target) ? target : null;
  const wikiBase = `/kb/collections/${encodeURIComponent(collectionId)}/wiki`;
  const openPath = useCallback(
    (p: string) => navigate(`${wikiBase}/${encodeLeafPath(p)}`),
    [navigate, wikiBase],
  );

  // Follow a [[wikilink]] to the page whose stem matches.
  const followLink = useCallback(
    (name: string) => {
      const t = visiblePages.find((p) => stem(p) === name);
      if (t) openPath(normPath(t));
    },
    [visiblePages, openPath],
  );

  // #402: draggable tree width, shared persisted key with the doc IDE.
  const [treeW, setTreeW] = usePersistentNumber("kb:ide:treeWidth", 260, 160, 560);
  const treeStart = useRef(treeW);

  if (pagesQuery.isPending) {
    return (
      <p className="kb-cols__empty" role="status" aria-live="polite">
        Loading the wiki…
      </p>
    );
  }

  return (
    <FileServiceProvider value={service}>
      <FileBufferProvider store={bufferStore}>
        <EditModeProvider>
          <DialogProvider>
            <div className="kb-ide">
              <div className="kb-ide__main">
                <div className="kb-ide__tree" style={{ width: treeW, flexShrink: 0 }}>
                  <FileTree
                    files={files}
                    dirs={dirs}
                    activePath={activePath}
                    onOpen={openPath}
                    onChanged={refetch}
                    searchable
                  />
                </div>
                <ResizeDivider
                  orientation="vertical"
                  ariaLabel="Resize page tree"
                  onResizeStart={() => {
                    treeStart.current = treeW;
                  }}
                  onResize={(d) => setTreeW(treeStart.current + d)}
                />
                <div className="kb-ide__pane">
                  {activePath ? (
                    <WikiEditorPane
                      path={activePath}
                      pages={filePaths}
                      docIdByPath={docIdByPath}
                      onNavigate={followLink}
                      onOpenDoc={onOpenDoc}
                    />
                  ) : (
                    <div className="kb-ide__empty">Select a page to read or edit.</div>
                  )}
                </div>
              </div>
            </div>
          </DialogProvider>
        </EditModeProvider>
      </FileBufferProvider>
    </FileServiceProvider>
  );
}

function WikiEditorPane({
  path,
  pages,
  docIdByPath,
  onNavigate,
  onOpenDoc,
}: {
  path: string;
  pages: string[];
  docIdByPath: Map<string, string>;
  onNavigate: (name: string) => void;
  onOpenDoc?: (documentId: string) => void;
}) {
  const { entry, setText, save } = useFileBuffer(path);
  const dirty = useIsDirty(path);
  const { isEditing, toggle } = useEditMode();
  const editing = isEditing(path);

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
        <span className="kb-ide__spacer" />
        <button type="button" className="kb-btn" onClick={() => toggle(path)}>
          {editing ? "Preview" : "Edit"}
        </button>
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
        {entry.status === "loading" ? (
          <p className="kb-cols__empty">Loading {path}…</p>
        ) : editing ? (
          <div style={{ height: "100%", minHeight: 0 }}>
            <MonacoEditor value={entry.text} onChange={setText} language="markdown" minHeight={0} />
          </div>
        ) : (
          <div style={{ padding: "24px 32px", maxWidth: 760 }}>
            <WikiPageBody
              content={entry.text}
              pages={pages}
              onNavigate={onNavigate}
              docIdByPath={docIdByPath}
              onOpenDoc={onOpenDoc}
            />
          </div>
        )}
      </div>
    </div>
  );
}
