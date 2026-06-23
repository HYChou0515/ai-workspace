/**
 * KB management — a grid of collection cards (icon, description, doc/size/cited
 * chips, owner, last-updated; pinnable), matching the design handoff. Clicking a
 * card opens a full CollectionPage: pickable icon, a stats banner
 * (Documents/Size/Chunks/Cited/Owner/Updated), rename + delete, and the
 * documents table. Rename/icon/delete go through specstar's native resource
 * CRUD (PATCH/DELETE /collection/{id}), not custom endpoints. Sharing /
 * permissions / activity are intentionally not modelled.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { mapWithConcurrency } from "../../api/concurrency";
import { kbApi, type KbApi, type KbDocument } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon, type IconName } from "../../components/Icon";
import { Popover } from "../../components/Popover";
import { Skeleton } from "../../components/Skeleton";
import { UserAvatar } from "../../components/UserChip";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { usePersistentSet } from "../../hooks/usePersistentSet";
import { ContextCardsTab } from "./ContextCardsTab";
import { fetchAllDocs, KbDocIde } from "./KbDocIde";
import { NewCollectionModal } from "./NewCollectionModal";
import { RetrievalToggles, WikiBadge } from "./RetrievalToggles";
import { WikiBrowser } from "./WikiBrowser";

// The file picker deliberately has NO `accept` filter. An extension-only
// allow-list (which is all the BE could enforce anyway — it sniffs + stores
// every type) made macOS grey out valid files: its picker maps each extension
// to a UTI and chokes on unusual ones (.jsonl/.tsv/.tgz/.tsx) and formats not
// in the list (.heic), so even images couldn't be selected. The server accepts
// anything, so the soft hint isn't worth the cross-platform breakage.

/** The destination path for one uploaded file. A folder pick carries each
 * file's `webkitRelativePath` (so the tree structure is preserved); fall back
 * to the bare name when it's empty — e.g. a single file chosen in the folder
 * dialog, which otherwise produced an empty path. Mirrors FileTree's rule. */
export function uploadDocPath(
  file: { name: string; webkitRelativePath?: string },
  asFolder: boolean,
): string {
  return (asFolder && file.webkitRelativePath) || file.name;
}

const ICON_OPTIONS: IconName[] = [
  "layers", "file", "folder", "flame", "bug", "check",
  "settings", "users", "tag", "sparkle", "branch", "git",
  "chat", "filter", "clock", "quote",
];

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${Math.round(n / (1024 * 1024))} MB`;
}

function fmtDate(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Compact count for the token estimate (≈ bytes/4): 12_400_000 → "12.4 M". */
function fmtCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)} M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)} K`;
  return String(n);
}

// One-line "what + when" blurb under the collection tabs (#162) — orient the
// reader on Documents / Context Cards / Wiki at the point of use. No system
// nouns (chunk / embed / index internals) — describe the outcome.
const TAB_BLURB: Record<"documents" | "cards" | "wiki", string> = {
  documents: "The files you've uploaded. Search reads these to answer questions.",
  cards: "A glossary you write by hand — exact terms the assistant uses verbatim when they come up.",
  wiki: "An AI-built, cross-linked summary the assistant reads for the big picture. Updates as you upload.",
};

type Tab = "all" | "mine" | "pinned";

export function KbCollectionsPage({
  client = kbApi,
  onOpenDoc,
}: {
  client?: KbApi;
  onOpenDoc?: (documentId: string) => void;
}) {
  const qc = useQueryClient();
  const me = useCurrentUser();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collectionTab, setCollectionTab] = useState<"documents" | "cards" | "wiki">("documents");
  const [showRetrieval, setShowRetrieval] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("all");
  const [ownerFilter, setOwnerFilter] = useState<string | null>(null);
  const [colQuery, setColQuery] = useState("");
  const [iconOpen, setIconOpen] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [editingDesc, setEditingDesc] = useState(false);
  const [descDraft, setDescDraft] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const importNewRef = useRef<HTMLInputElement>(null);
  const importIntoRef = useRef<HTMLInputElement>(null);
  // #101: a zip picked for "import into this collection", held until the user
  // chooses how a path collision resolves (overwrite | skip). null ⇒ no dialog.
  const [importFile, setImportFile] = useState<File | null>(null);
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
  // tabs where KbDocIde isn't mounted. Disabled on the grid (no open collection).
  const docStatusQuery = useQuery({
    queryKey: qk.kb.documents(selectedId ?? "__none__"),
    enabled: selectedId != null,
    queryFn: () => fetchAllDocs(client, selectedId as string),
    refetchInterval: (q) =>
      (q.state.data as KbDocument[] | undefined)?.some((d) => d.status === "indexing")
        ? 1500
        : false,
  });
  const statusDocs = (docStatusQuery.data ?? []) as KbDocument[];
  const indexingCount = statusDocs.filter((d) => d.status === "indexing").length;
  const erroredCount = statusDocs.filter((d) => d.status === "error").length;

  // Reset the open collection's transient UI when switching away. (The doc
  // tree + editor own their own state inside KbDocIde.)
  useEffect(() => {
    setIconOpen(false);
    setEditingName(false);
    setEditingDesc(false);
    setConfirmDel(false);
    setImportFile(null);
  }, [selectedId]);

  const createMut = useMutation({
    mutationFn: (v: { name: string; description: string; useRag: boolean; useWiki: boolean }) =>
      client.createCollection(v.name, v.description, { useRag: v.useRag, useWiki: v.useWiki }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
  });

  const updateMut = useMutation({
    mutationFn: (patch: {
      name?: string;
      icon?: string;
      description?: string;
      use_rag?: boolean;
      use_wiki?: boolean;
    }) => client.updateCollection(selectedId as string, patch),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
  });

  const deleteMut = useMutation({
    mutationFn: () => client.deleteCollection(selectedId as string),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
      setSelectedId(null);
    },
  });

  const reindexAllMut = useMutation({
    mutationFn: () => client.reindexCollection(selectedId as string),
    onSuccess: () => {
      if (selectedId) void qc.invalidateQueries({ queryKey: qk.kb.documents(selectedId) });
    },
  });

  // #101: two-step download — prepare (build the zip server-side, the slow part
  // the loading state covers) then stream it via a native anchor so even a large
  // export writes straight to disk instead of buffering in JS memory.
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

  // #101: import a zip as a NEW collection (landing page). On success open it.
  const importNewMut = useMutation({
    mutationFn: (file: File) => client.importCollectionNew(file),
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
      setSelectedId(res.collection_id);
    },
  });

  const pickImportNew = (files: FileList | null) => {
    const file = files?.[0];
    if (file) importNewMut.mutate(file);
    // Clear so re-picking the same file fires onChange again.
    if (importNewRef.current) importNewRef.current.value = "";
  };

  // #101: merge a zip INTO the open collection. Picking a file stages it; the
  // mode dialog then commits with overwrite|skip (overwrite is destructive, so
  // the user confirms the choice rather than it being silent).
  const importIntoMut = useMutation({
    mutationFn: (vars: { file: File; mode: "overwrite" | "skip" }) =>
      client.importCollectionInto(selectedId as string, vars.file, vars.mode),
    onSuccess: () => {
      if (selectedId) void qc.invalidateQueries({ queryKey: qk.kb.documents(selectedId) });
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
        client.uploadDocument(selectedId as string, file, uploadDocPath(file, vars.asFolder)),
      ),
    onSuccess: () => {
      if (selectedId) void qc.invalidateQueries({ queryKey: qk.kb.documents(selectedId) });
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
    },
  });

  const busy = uploadMut.isPending;

  const upload = (files: FileList | null, asFolder = false) => {
    if (!files || !selectedId || busy) return;
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

  const selected = collections.find((c) => c.resource_id === selectedId) ?? null;
  const mostCited = collections.reduce<(typeof collections)[number] | null>(
    (best, c) => (c.cited > (best?.cited ?? 0) ? c : best),
    null,
  );
  const sorted = [...collections].sort(
    (a, b) =>
      Number(pinned.has(b.resource_id)) - Number(pinned.has(a.resource_id)) ||
      a.name.localeCompare(b.name),
  );
  // Library-wide aggregates for the landing header.
  const totalDocs = collections.reduce((s, c) => s + c.doc_count, 0);
  const totalSize = collections.reduce((s, c) => s + c.size, 0);
  const mineCount = collections.filter((c) => c.owner === me).length;
  const sharedCount = collections.length - mineCount;
  const pinnedCount = collections.filter((c) => pinned.has(c.resource_id)).length;
  const owners = [...new Set(collections.map((c) => c.owner))].sort();
  // Tab + owner + name filters compose over the pinned-first sorted list.
  const cq = colQuery.trim().toLowerCase();
  const shownCols = sorted.filter((c) => {
    if (tab === "mine" && c.owner !== me) return false;
    if (tab === "pinned" && !pinned.has(c.resource_id)) return false;
    if (ownerFilter && c.owner !== ownerFilter) return false;
    if (cq && !c.name.toLowerCase().includes(cq)) return false;
    return true;
  });
  // ---- the full collection page (a collection is open) ----
  if (selected) {
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
    // exists for collections that build one. A stale "wiki" selection on a
    // non-wiki collection falls back to Documents.
    const tabIds = (
      selected.use_wiki ? ["documents", "cards", "wiki"] : ["documents", "cards"]
    ) as ("documents" | "cards" | "wiki")[];
    const effectiveTab = tabIds.includes(collectionTab) ? collectionTab : "documents";
    return (
      <section className="kb-colpage" aria-label="Collection">
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

        <button type="button" className="kb-nav__back" onClick={() => setSelectedId(null)}>
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
                    <Icon name="layers" size={14} color="var(--text-paper-d)" /> Retrieval modes
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
                busy ? "Uploading…" : null,
                indexingCount > 0 ? `Indexing ${indexingCount}…` : null,
                erroredCount > 0 ? `${erroredCount} failed to index` : null,
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
              <span className="caps">Retrieval modes</span>
              <button
                type="button"
                className="kb-btn"
                aria-label="Close retrieval modes"
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
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={effectiveTab === id}
              className={`kb-tab${effectiveTab === id ? " is-active" : ""}`}
              onClick={() => setCollectionTab(id)}
            >
              {id === "documents" ? "Documents" : id === "cards" ? "Context Cards" : "Wiki"}
            </button>
          ))}
        </div>

        <p className="kb-tabs__blurb">{TAB_BLURB[effectiveTab]}</p>

        {effectiveTab === "wiki" ? (
          <div className="kb-colpage__docs">
            <WikiBrowser
              collectionId={selected.resource_id}
              collectionName={selected.name}
              onOpenDoc={onOpenDoc}
              client={client}
              maintainerGuidance={selected.wiki_maintainer_guidance}
              readerGuidance={selected.wiki_reader_guidance}
            />
          </div>
        ) : effectiveTab === "cards" ? (
          <div className="kb-colpage__docs">
            <ContextCardsTab collectionId={selected.resource_id} client={client} />
          </div>
        ) : (
          // Documents as a VSCode-shaped tree + editor (#87) — the same shell
          // the investigation workspace uses, over this collection's docs.
          // Per-doc chunks / cited / full metadata live in the full-page viewer
          // (the upload button + reindex menu stay in the page chrome above).
          <div className="kb-colpage__docs">
            <KbDocIde collectionId={selected.resource_id} client={client} />
          </div>
        )}
      </section>
    );
  }

  // ---- collection grid (landing) ----
  const tabs: [Tab, string, number][] = [
    ["all", "All", collections.length],
    ["mine", "Mine", mineCount],
    ["pinned", "Pinned", pinnedCount],
  ];
  return (
    <section className="kb-grid-page" aria-label="Collections">
      <header className="kb-libhead">
        <div className="kb-libhead__intro">
          <div className="caps">Knowledge base</div>
          <h1 className="kb-libhead__title">
            {collections.length} collections <span className="kb-libhead__dot">·</span> {totalDocs}{" "}
            documents
          </h1>
          <p className="kb-libhead__lead">
            Collections are the unit of search. Pick which to use as context when chatting.
          </p>
        </div>
        <div className="kb-libhead__metrics">
          <div className="kb-metric">
            <span className="kb-metric__label">My collections</span>
            <span className="kb-metric__value">{mineCount}</span>
            <span className="kb-metric__sub">plus {sharedCount} shared</span>
          </div>
          <div className="kb-metric">
            <span className="kb-metric__label">Total size</span>
            <span className="kb-metric__value">{fmtBytes(totalSize)}</span>
            <span className="kb-metric__sub">≈ {fmtCount(Math.round(totalSize / 4))} tokens</span>
          </div>
          <div className="kb-metric">
            <span className="kb-metric__label">Most cited</span>
            <span className="kb-metric__value" title={mostCited?.name}>
              {mostCited && mostCited.cited > 0 ? mostCited.name : "—"}
            </span>
            <span className="kb-metric__sub">
              {mostCited && mostCited.cited > 0 ? `${mostCited.cited} citations` : "no citations yet"}
            </span>
          </div>
        </div>
      </header>

      <div className="kb-tabs">
        {tabs.map(([id, label, count]) => (
          <button
            key={id}
            type="button"
            className={`kb-tab${tab === id ? " is-active" : ""}`}
            aria-pressed={tab === id}
            onClick={() => setTab(id)}
          >
            {label} <span className="kb-tab__count">{count}</span>
          </button>
        ))}
      </div>

      <div className="kb-cols__actions">
        <label className="kb-docsearch kb-docsearch--inline">
          <Icon name="search" size={14} color="var(--text-paper-d)" />
          <input
            type="search"
            placeholder="Filter collections…"
            value={colQuery}
            onChange={(e) => setColQuery(e.target.value)}
          />
        </label>
        <Popover
          align="start"
          trigger={({ onClick, open }) => (
            <button type="button" className="kb-btn" aria-haspopup="menu" aria-expanded={open} onClick={onClick}>
              <Icon name="user" size={13} /> Owner · {ownerFilter ?? "any"} <Icon name="chev_d" size={11} />
            </button>
          )}
        >
          {(close) => (
            <div className="kb-menu" role="menu">
              <button type="button" role="menuitem" className="kb-menu__item" onClick={() => { close(); setOwnerFilter(null); }}>
                Any owner
              </button>
              {owners.map((o) => (
                <button key={o} type="button" role="menuitem" className="kb-menu__item" onClick={() => { close(); setOwnerFilter(o); }}>
                  {o}
                </button>
              ))}
            </div>
          )}
        </Popover>
        <span style={{ flex: 1 }} />
        <input
          ref={importNewRef}
          type="file"
          accept=".zip,application/zip"
          hidden
          aria-label="Import collection from file"
          onChange={(e) => pickImportNew(e.target.files)}
        />
        <button type="button" className="kb-btn" disabled={importNewMut.isPending} onClick={() => importNewRef.current?.click()}>
          <Icon name="upload" size={13} /> Import
        </button>
        <button type="button" className="kb-btn kb-btn--primary" onClick={() => setNewOpen(true)}>
          <Icon name="plus" size={13} /> New collection
        </button>
      </div>

      <NewCollectionModal
        open={newOpen}
        busy={createMut.isPending}
        onClose={() => setNewOpen(false)}
        onCreate={(name, description, opts) =>
          createMut.mutate(
            { name, description, useRag: opts.useRag, useWiki: opts.useWiki },
            { onSuccess: () => setNewOpen(false) },
          )
        }
      />

      {collectionsLoading ? (
        <div className="kb-grid" aria-busy="true" data-testid="kb-cols-loading">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="kb-card-wrap">
              <Skeleton className="kb-skel--card" />
            </div>
          ))}
        </div>
      ) : collections.length === 0 ? (
        <p className="kb-cols__empty">No collections yet — create one to start adding documents.</p>
      ) : shownCols.length === 0 ? (
        <p className="kb-cols__empty">No collections match the current filters.</p>
      ) : (
        <div className="kb-grid">
          {shownCols.map((c) => (
            <div key={c.resource_id} className="kb-card-wrap">
              <button type="button" className="kb-card" aria-label={`Open ${c.name}`} onClick={() => setSelectedId(c.resource_id)}>
                <div className="kb-card__icon">
                  <Icon name={c.icon as IconName} size={18} color="var(--accent-h)" />
                </div>
                <div className="kb-card__name">{c.name}</div>
                <div className="kb-card__desc">{c.description}</div>
                <div className="kb-card__chips">
                  <span className="kb-chip">
                    <Icon name="file" size={10} color="var(--text-paper-d2)" /> {c.doc_count} docs
                  </span>
                  <span className="kb-chip">{fmtBytes(c.size)}</span>
                  {c.use_wiki && <WikiBadge />}
                  {c.cited > 0 && (
                    <span className="kb-chip kb-chip--accent">
                      <Icon name="quote" size={10} color="var(--accent-h)" /> cited {c.cited}×
                    </span>
                  )}
                </div>
                <div className="kb-card__foot">
                  <UserAvatar userId={c.owner} size={20} />
                  <span className="kb-card__owner">{c.owner}</span>
                  <span style={{ flex: 1 }} />
                  <span className="kb-card__updated">{fmtDate(c.updated_at)}</span>
                </div>
              </button>
              <button
                type="button"
                className={`kb-card__pin${pinned.has(c.resource_id) ? " is-pinned" : ""}`}
                aria-label={`${pinned.has(c.resource_id) ? "Unpin" : "Pin"} ${c.name}`}
                aria-pressed={pinned.has(c.resource_id)}
                onClick={() => pinned.toggle(c.resource_id)}
              >
                <Icon name="pin" size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
