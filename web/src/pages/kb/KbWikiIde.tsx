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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { FileServiceProvider } from "../../api/fileService";
import { kbApi, type KbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { normPath } from "../../api/kbFileService";
import { wikiFileService } from "../../api/wikiFileService";
import { MonacoEditor } from "../../components/MonacoEditor";
import { DialogProvider } from "../../components/Dialog";
import { EditModeProvider, useEditMode } from "../../hooks/editMode";
import { FileBufferProvider, FileBufferStore, useFileBuffer, useIsDirty } from "../../hooks/fileBuffer";
import { FileTree } from "../investigation/FileTree";
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

  const [activePath, setActivePath] = useState<string | null>(null);
  // A just-created / moved page isn't in the list until the refetch lands;
  // defer the open until it appears (same race the doc IDE hit).
  const [pendingOpen, setPendingOpen] = useState<string | null>(null);
  const openPath = useCallback(
    (p: string) => (pageSet.has(p) ? setActivePath(p) : setPendingOpen(p)),
    [pageSet],
  );
  useEffect(() => {
    if (pendingOpen && pageSet.has(pendingOpen)) {
      setActivePath(pendingOpen);
      setPendingOpen(null);
    }
  }, [pendingOpen, pageSet]);
  // Keep a valid page selected: hold the current one if it still exists, else
  // fall back to index/first (or nothing). Skipped while a deferred open waits.
  useEffect(() => {
    if (pendingOpen) return;
    if (activePath && pageSet.has(activePath)) return;
    const next = visiblePages.find((p) => p.endsWith("/index.md")) ?? visiblePages[0];
    setActivePath(next ? normPath(next) : null);
  }, [activePath, pendingOpen, pageSet, visiblePages]);

  // Follow a [[wikilink]] to the page whose stem matches.
  const navigate = useCallback(
    (name: string) => {
      const target = visiblePages.find((p) => stem(p) === name);
      if (target) openPath(normPath(target));
    },
    [visiblePages, openPath],
  );

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
                <div className="kb-ide__tree">
                  <FileTree
                    files={files}
                    dirs={dirs}
                    activePath={activePath}
                    onOpen={openPath}
                    onChanged={refetch}
                  />
                </div>
                <div className="kb-ide__pane">
                  {activePath ? (
                    <WikiEditorPane
                      path={activePath}
                      pages={filePaths}
                      docIdByPath={docIdByPath}
                      onNavigate={navigate}
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
