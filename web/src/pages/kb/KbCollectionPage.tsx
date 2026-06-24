/**
 * The open-collection page (route /kb/collections/:cid) — pickable icon, a
 * stats banner (Documents/Size/Cited/Owner/Updated), inline rename + description,
 * a settings menu (rename / retrieval modes / re-index / delete) and Upload.
 * Its Documents / Context Cards / Wiki tabs are their OWN routes (#93): this
 * component is the layout (chrome + tab bar of NavLinks) and renders the matched
 * tab through <Outlet/>. Rename/icon/delete go through specstar's native
 * resource CRUD (PATCH/DELETE /collection/{id}), not custom endpoints.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import {
  NavLink,
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useOutletContext,
  useParams,
} from "react-router-dom";

import { mapWithConcurrency } from "../../api/concurrency";
import { kbApi, type KbApi, type KbCitation, type KbCollection, type KbDocument } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon, type IconName } from "../../components/Icon";
import { Popover } from "../../components/Popover";
import { usePersistentSet } from "../../hooks/usePersistentSet";
import { useT } from "../../lib/i18n";
import { fmtBytes, fmtDate, ICON_OPTIONS, uploadDocPath } from "./collectionFormat";
import { ContextCardsTab } from "./ContextCardsTab";
import { fetchAllDocs, KbDocIde } from "./KbDocIde";
import { useKbOutlet } from "./KbHome";
import { RetrievalToggles } from "./RetrievalToggles";
import { WikiBrowser } from "./WikiBrowser";

// One-line "what + when" blurb under the collection tabs (#162) — orient the
// reader on Documents / Context Cards / Wiki at the point of use. No system
// nouns (chunk / embed / index internals) — describe the outcome.
const TAB_BLURB: Record<"documents" | "cards" | "wiki", string> = {
  documents: "The files you've uploaded. Search reads these to answer questions.",
  cards: "A glossary you write by hand — exact terms the assistant uses verbatim when they come up.",
  wiki: "An AI-built, cross-linked summary the assistant reads for the big picture. Updates as you upload.",
};

/** What the collection layout shares with its routed tab children: the open
 * collection, the API client, and the shell's doc-viewer openers (re-provided
 * because a nested Outlet context shadows the shell's). */
export type KbCollectionCtx = {
  collection: KbCollection;
  client: KbApi;
  openDoc: (documentId: string) => void;
  openCite: (c: KbCitation) => void;
};
export function useCollectionOutlet(): KbCollectionCtx {
  return useOutletContext<KbCollectionCtx>();
}

export function KbCollectionPage({ client = kbApi }: { client?: KbApi }) {
  const t = useT();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { openDoc, openCite } = useKbOutlet();
  // The open collection is the URL (#93): /kb/collections/:cid.
  const { cid } = useParams();
  const { pathname } = useLocation();
  const [showRetrieval, setShowRetrieval] = useState(false);
  const [iconOpen, setIconOpen] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [editingDesc, setEditingDesc] = useState(false);
  const [descDraft, setDescDraft] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);
  // #101: a zip staged for "import into this collection", held until the user
  // picks how a path collision resolves (overwrite | skip). null ⇒ no dialog.
  const [importFile, setImportFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const importIntoRef = useRef<HTMLInputElement>(null);
  const pinned = usePersistentSet("kb:pinned-collections");

  const folderInputRef = (el: HTMLInputElement | null) => {
    folderRef.current = el;
    if (el) {
      el.webkitdirectory = true;
      el.setAttribute("webkitdirectory", "");
    }
  };

  const { data: collections = [], isPending: collectionsLoading } = useQuery({
    queryKey: qk.kb.collections,
    queryFn: () => client.listCollections(),
  });

  // Open collection's doc statuses, for the index-status strip (#162). Shares
  // the exact query (key + fetcher) KbDocIde uses, so the two dedupe into one
  // fetch + one poll; this observer keeps the strip live even on the Cards/Wiki
  // tabs where KbDocIde isn't mounted.
  const docStatusQuery = useQuery({
    queryKey: qk.kb.documents(cid ?? "__none__"),
    enabled: cid != null,
    queryFn: () => fetchAllDocs(client, cid as string),
    refetchInterval: (q) =>
      (q.state.data as KbDocument[] | undefined)?.some((d) => d.status === "indexing")
        ? 1500
        : false,
  });
  const statusDocs = (docStatusQuery.data ?? []) as KbDocument[];
  const indexingCount = statusDocs.filter((d) => d.status === "indexing").length;
  const erroredCount = statusDocs.filter((d) => d.status === "error").length;

  // Reset the transient UI when switching to a different collection. (The doc
  // tree + editor own their own state inside KbDocIde.)
  useEffect(() => {
    setIconOpen(false);
    setEditingName(false);
    setEditingDesc(false);
    setConfirmDel(false);
    setShowRetrieval(false);
    setImportFile(null);
  }, [cid]);

  const updateMut = useMutation({
    mutationFn: (patch: {
      name?: string;
      icon?: string;
      description?: string;
      use_rag?: boolean;
      use_wiki?: boolean;
    }) => client.updateCollection(cid as string, patch),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
  });

  const deleteMut = useMutation({
    mutationFn: () => client.deleteCollection(cid as string),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
      navigate("/kb/collections");
    },
  });

  const reindexAllMut = useMutation({
    mutationFn: () => client.reindexCollection(cid as string),
    onSuccess: () => {
      if (cid) void qc.invalidateQueries({ queryKey: qk.kb.documents(cid) });
    },
  });

  // #101: two-step download — prepare (build the zip server-side) then stream it
  // via a native anchor so even a large export writes straight to disk.
  const downloadMut = useMutation({
    mutationFn: async (collectionId: string) => {
      const prep = await client.prepareCollectionDownload(collectionId);
      return {
        url: client.streamCollectionDownloadUrl(collectionId, prep.download_id),
        filename: prep.filename,
      };
    },
    onSuccess: ({ url, filename }) => {
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
    },
  });

  // #101: merge a zip INTO the open collection. Picking a file stages it; the
  // mode dialog then commits with overwrite|skip (overwrite is destructive, so
  // the user confirms rather than it being silent).
  const importIntoMut = useMutation({
    mutationFn: (vars: { file: File; mode: "overwrite" | "skip" }) =>
      client.importCollectionInto(cid as string, vars.file, vars.mode),
    onSuccess: () => {
      if (cid) void qc.invalidateQueries({ queryKey: qk.kb.documents(cid) });
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
      setImportFile(null);
    },
  });
  const pickImportInto = (files: FileList | null) => {
    const file = files?.[0];
    if (file) setImportFile(file);
    if (importIntoRef.current) importIntoRef.current.value = "";
  };
  const runImportInto = (mode: "overwrite" | "skip") => {
    if (importFile) importIntoMut.mutate({ file: importFile, mode });
  };

  const uploadMut = useMutation({
    // Bounded concurrency: a folder pick can be hundreds of files — firing them
    // all at once froze the tab and flushed nothing. A small pool keeps the UI
    // alive while still uploading everything.
    mutationFn: (vars: { files: File[]; asFolder: boolean }) =>
      mapWithConcurrency(vars.files, 4, (file) =>
        client.uploadDocument(cid as string, file, uploadDocPath(file, vars.asFolder)),
      ),
    onSuccess: () => {
      if (cid) void qc.invalidateQueries({ queryKey: qk.kb.documents(cid) });
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
    },
  });

  const busy = uploadMut.isPending;

  const upload = (files: FileList | null, asFolder = false) => {
    if (!files || !cid || busy) return;
    uploadMut.mutate(
      { files: Array.from(files), asFolder },
      {
        // Clear both inputs so re-picking the SAME file/folder fires onChange
        // again (a value left in place would suppress the next selection).
        onSettled: () => {
          if (fileRef.current) fileRef.current.value = "";
          if (folderRef.current) folderRef.current.value = "";
        },
      },
    );
  };

  const selected = collections.find((c) => c.resource_id === cid) ?? null;
  if (!selected) {
    // While the collections list is still loading the open id may not resolve
    // yet; once it has, an unknown id bounces back to the grid.
    return collectionsLoading ? (
      <section className="kb-colpage" aria-label="Collection">
        <div className="kb-colpage__docs">Loading…</div>
      </section>
    ) : (
      <Navigate to="/kb/collections" replace />
    );
  }

  const isPinned = pinned.has(selected.resource_id);
  const commitRename = () => {
    const name = nameDraft.trim();
    setEditingName(false);
    if (name && name !== selected.name) updateMut.mutate({ name });
  };
  const commitDesc = () => {
    const description = descDraft.trim();
    setEditingDesc(false);
    if (description !== selected.description) updateMut.mutate({ description });
  };
  const stats: [string, string, boolean?][] = [
    ["Documents", String(selected.doc_count)],
    ["Size", fmtBytes(selected.size)],
    ["Cited", `${selected.cited}×`, selected.cited > 0],
    ["Owner", selected.owner],
    ["Updated", fmtDate(selected.updated_at)],
  ];
  // Documents + Context Cards (#106) are always available; the Wiki tab only
  // exists for collections that build one.
  const tabIds = (
    selected.use_wiki ? ["documents", "cards", "wiki"] : ["documents", "cards"]
  ) as ("documents" | "cards" | "wiki")[];
  // Which tab is open (for the blurb) — the path segment after the collection id.
  const tabSeg = pathname.split("/")[4];
  const activeTab: "documents" | "cards" | "wiki" =
    tabSeg === "cards" || tabSeg === "wiki" ? tabSeg : "documents";

  return (
    <section className="kb-colpage" aria-label="Collection">
      {/* No `accept` filter on the pickers: an extension allow-list (all the BE
          could enforce — it sniffs + stores every type) made macOS grey out
          valid files, mapping each extension to a UTI and choking on unusual
          ones (.jsonl/.tsv/.tgz/.tsx) and formats not in the list (.heic). */}
      <input ref={fileRef} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
      <input ref={folderInputRef} type="file" multiple hidden onChange={(e) => upload(e.target.files, true)} />
      <input
        ref={importIntoRef}
        type="file"
        accept=".zip,application/zip"
        hidden
        aria-label="Import into this collection"
        onChange={(e) => pickImportInto(e.target.files)}
      />

      <button type="button" className="kb-nav__back" onClick={() => navigate("/kb/collections")}>
        <Icon name="chev_l" size={13} /> Knowledge base
      </button>

      <div className="kb-colpage__head">
        <div className="kb-colpage__lead">
          <div className="kb-colpage__iconwrap">
            <button type="button" className="kb-colpage__icon" aria-label="Change icon" onClick={() => setIconOpen((v) => !v)}>
              <Icon name={selected.icon as IconName} size={26} color="var(--accent-h)" />
              <span className="kb-colpage__icon-badge">
                <Icon name="plus" size={9} color="var(--white)" />
              </span>
            </button>
            {iconOpen && (
              <div className="kb-iconpicker">
                <div className="caps" style={{ marginBottom: 8 }}>Choose an icon</div>
                <div className="kb-iconpicker__grid">
                  {ICON_OPTIONS.map((n) => (
                    <button
                      key={n}
                      type="button"
                      aria-label={`Icon ${n}`}
                      className={`kb-iconpicker__opt${selected.icon === n ? " is-on" : ""}`}
                      onClick={() => {
                        setIconOpen(false);
                        updateMut.mutate({ icon: n });
                      }}
                    >
                      <Icon name={n} size={14} />
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
          <div className="kb-colpage__titles">
            <div className="kb-colpage__chips">
              <button
                type="button"
                className={`kb-chip${isPinned ? " kb-chip--accent" : ""}`}
                aria-label={`${isPinned ? "Unpin" : "Pin"} ${selected.name}`}
                aria-pressed={isPinned}
                onClick={() => pinned.toggle(selected.resource_id)}
              >
                <Icon name="pin" size={10} /> {isPinned ? "pinned" : "pin"}
              </button>
            </div>
            {editingName ? (
              <input
                className="kb-colpage__nameedit"
                // biome-ignore lint/a11y/noAutofocus: rename input should grab focus
                autoFocus
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onBlur={commitRename}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitRename();
                  if (e.key === "Escape") setEditingName(false);
                }}
              />
            ) : (
              <h1
                className="kb-colpage__title"
                title="Click to rename"
                onClick={() => {
                  setNameDraft(selected.name);
                  setEditingName(true);
                }}
              >
                {selected.name}
              </h1>
            )}
            {editingDesc ? (
              <textarea
                className="kb-colpage__descedit"
                // biome-ignore lint/a11y/noAutofocus: description editor should grab focus
                autoFocus
                rows={2}
                value={descDraft}
                placeholder="Add a description…"
                onChange={(e) => setDescDraft(e.target.value)}
                onBlur={commitDesc}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) commitDesc();
                  if (e.key === "Escape") setEditingDesc(false);
                }}
              />
            ) : (
              <p
                className={`kb-colpage__desc${selected.description ? "" : " is-empty"}`}
                title="Click to edit"
                onClick={() => {
                  setDescDraft(selected.description);
                  setEditingDesc(true);
                }}
              >
                {selected.description || "Add a description…"}
              </p>
            )}
          </div>
        </div>

        <div className="kb-colpage__actions">
          <Popover
            align="end"
            trigger={({ onClick, open }) => (
              <button
                type="button"
                className="kb-btn"
                aria-label="Collection settings"
                aria-haspopup="menu"
                aria-expanded={open}
                onClick={onClick}
              >
                <Icon name="settings" size={13} />
              </button>
            )}
          >
            {(close) => (
              <div className="kb-menu" role="menu">
                <button type="button" role="menuitem" className="kb-menu__item" onClick={() => { close(); setNameDraft(selected.name); setEditingName(true); }}>
                  <Icon name="tag" size={14} color="var(--text-paper-d)" /> Rename
                </button>
                <button type="button" role="menuitem" className="kb-menu__item" onClick={() => { close(); setShowRetrieval((v) => !v); }}>
                  <Icon name="layers" size={14} color="var(--text-paper-d)" /> {t("kb.retrieval.title")}
                </button>
                <button type="button" role="menuitem" className="kb-menu__item" disabled={selected.doc_count === 0 || reindexAllMut.isPending} onClick={() => { close(); reindexAllMut.mutate(); }}>
                  <Icon name="refresh" size={14} color="var(--text-paper-d)" /> Re-index all
                </button>
                <button type="button" role="menuitem" className="kb-menu__item" disabled={downloadMut.isPending} onClick={() => { close(); downloadMut.mutate(selected.resource_id); }}>
                  <Icon name="download" size={14} color="var(--text-paper-d)" /> Download collection
                </button>
                <button type="button" role="menuitem" className="kb-menu__item" onClick={() => { close(); importIntoRef.current?.click(); }}>
                  <Icon name="upload" size={14} color="var(--text-paper-d)" /> Import into this collection
                </button>
                <div className="kb-menu__divider" />
                <button type="button" role="menuitem" className="kb-menu__item kb-menu__item--danger" onClick={() => { close(); setConfirmDel(true); }}>
                  <Icon name="x" size={14} /> Delete collection
                </button>
              </div>
            )}
          </Popover>

          <Popover
            align="end"
            trigger={({ onClick, open }) => (
              <button
                type="button"
                className="kb-btn kb-btn--primary"
                disabled={busy}
                aria-haspopup="menu"
                aria-expanded={open}
                onClick={onClick}
              >
                <Icon name="upload" size={13} /> Upload <Icon name="chev_d" size={11} />
              </button>
            )}
          >
            {(close) => (
              <div className="kb-menu" role="menu">
                <button type="button" role="menuitem" className="kb-menu__item" onClick={() => { close(); fileRef.current?.click(); }}>
                  <Icon name="file" size={14} color="var(--text-paper-d)" /> Upload files
                </button>
                <button type="button" role="menuitem" className="kb-menu__item" onClick={() => { close(); folderRef.current?.click(); }}>
                  <Icon name="folder" size={14} color="var(--text-paper-d)" /> Upload folder
                </button>
              </div>
            )}
          </Popover>

          {confirmDel && (
            <div className="kb-colpage__confirm" role="dialog" aria-label="Confirm delete collection">
              <span>Delete “{selected.name}”?</span>
              <button type="button" className="kb-btn kb-btn--danger" disabled={deleteMut.isPending} onClick={() => deleteMut.mutate()}>
                Delete
              </button>
              <button type="button" className="kb-btn" onClick={() => setConfirmDel(false)}>
                Cancel
              </button>
            </div>
          )}

          {importFile && (
            <div className="kb-colpage__confirm" role="dialog" aria-label="Import into collection">
              <span>Import “{importFile.name}” — for documents that already exist?</span>
              <button type="button" className="kb-btn kb-btn--danger" disabled={importIntoMut.isPending} onClick={() => runImportInto("overwrite")}>
                Overwrite
              </button>
              <button type="button" className="kb-btn" disabled={importIntoMut.isPending} onClick={() => runImportInto("skip")}>
                Skip existing
              </button>
              <button type="button" className="kb-btn" onClick={() => setImportFile(null)}>
                Cancel
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="kb-colpage__stats">
        {stats.map(([label, value, hot]) => (
          <div key={label} className="kb-stat">
            <span className="kb-stat__label">{label}</span>
            <span className={`kb-stat__value${hot ? " is-hot" : ""}`}>{value}</span>
          </div>
        ))}
      </div>

      {/* Index-status strip (#162): visible on every tab so the upload →
          indexing → ready/error transition is never invisible. Hidden once
          nothing is uploading / indexing / errored. */}
      {(busy || indexingCount > 0 || erroredCount > 0) && (
        <div
          className={`kb-index-status${erroredCount > 0 && indexingCount === 0 && !busy ? " is-error" : ""}`}
          data-testid="kb-index-status"
          role="status"
        >
          <Icon
            name={erroredCount > 0 && indexingCount === 0 && !busy ? "x" : "refresh"}
            size={13}
            color={erroredCount > 0 && indexingCount === 0 && !busy ? "var(--err)" : "var(--accent-h)"}
          />
          <span>
            {[
              busy ? t("kb.status.uploading") : null,
              indexingCount > 0 ? t("kb.status.indexing", { n: indexingCount }) : null,
              erroredCount > 0 ? t("kb.status.failed", { n: erroredCount }) : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </div>
      )}

      {showRetrieval && (
        <div
          style={{
            margin: "4px 0 8px",
            padding: 14,
            border: "1px solid var(--paper-3)",
            borderRadius: 8,
            background: "var(--paper-2)",
          }}
        >
          <div
            style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}
          >
            <span className="caps">{t("kb.retrieval.title")}</span>
            <button
              type="button"
              className="kb-btn"
              aria-label={t("kb.retrieval.close")}
              onClick={() => setShowRetrieval(false)}
            >
              <Icon name="x" size={12} />
            </button>
          </div>
          <RetrievalToggles
            docSearch={selected.use_rag}
            wiki={selected.use_wiki}
            onChange={({ docSearch, wiki }) => {
              // Keep at least one mode on (a collection that answers nothing
              // is a footgun); the toggle that would empty it is a no-op.
              if (!docSearch && !wiki) return;
              updateMut.mutate({ use_rag: docSearch, use_wiki: wiki });
            }}
          />
        </div>
      )}

      <div className="kb-tabs" role="tablist" aria-label="Collection view">
        {tabIds.map((id) => (
          <NavLink
            key={id}
            to={id}
            role="tab"
            className={({ isActive }) => `kb-tab${isActive ? " is-active" : ""}`}
          >
            {id === "documents" ? "Documents" : id === "cards" ? "Context Cards" : "Wiki"}
          </NavLink>
        ))}
      </div>

      <p className="kb-tabs__blurb">{TAB_BLURB[activeTab]}</p>

      <div className="kb-colpage__docs">
        <Outlet
          context={{ collection: selected, client, openDoc, openCite } satisfies KbCollectionCtx}
        />
      </div>
    </section>
  );
}

// ---- the tab routes (#93): each renders one tab's content from the layout's
// Outlet context, so the active tab is the URL, not component state. ----

export function DocumentsTab() {
  // Documents as a VSCode-shaped tree + editor (#87) — the same shell the
  // investigation workspace uses, over this collection's docs.
  const { collection, client } = useCollectionOutlet();
  return <KbDocIde collectionId={collection.resource_id} client={client} />;
}

export function CardsTab() {
  const { collection, client } = useCollectionOutlet();
  return <ContextCardsTab collectionId={collection.resource_id} client={client} />;
}

export function WikiTab() {
  const { collection, client, openDoc } = useCollectionOutlet();
  // A wiki URL on a non-wiki collection falls back to Documents.
  if (!collection.use_wiki) return <Navigate to="../documents" replace />;
  return (
    <WikiBrowser
      collectionId={collection.resource_id}
      collectionName={collection.name}
      onOpenDoc={openDoc}
      client={client}
      maintainerGuidance={collection.wiki_maintainer_guidance}
      readerGuidance={collection.wiki_reader_guidance}
    />
  );
}
