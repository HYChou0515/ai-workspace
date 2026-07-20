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
import {
  kbApi,
  UploadBlockedError,
  type KbApi,
  type KbCitation,
  type KbCollection,
  type ReindexQueued,
} from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { mergeBlocked, screenFiles, type BlockedUpload } from "../../kb/uploadChecks";
import { UploadBlockedList } from "./UploadBlockedList";
import { DialogProvider, useDialog } from "../../components/Dialog";
import { Icon, type IconName } from "../../components/Icon";
import { PermissionDialog } from "../../components/PermissionDialog";
import { Popover } from "../../components/Popover";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { usePersistentBoolean } from "../../hooks/usePersistentBoolean";
import { usePersistentSet } from "../../hooks/usePersistentSet";
import { type MsgKey, useT } from "../../lib/i18n";
import type { CollectionPermission } from "../../lib/permission";
import { fmtBytes, fmtDate, ICON_OPTIONS, uploadDocPath } from "./collectionFormat";
import { CodeConnectionEditor } from "./CodeConnectionEditor";
import { CodeSyncStatus } from "./CodeSyncStatus";
import { CollectionReviewTab } from "./CollectionReviewTab";
import { ContextCardsTab } from "./ContextCardsTab";
import { CardGenToggle } from "./CardGenToggle";
import { GlobalToggle } from "./GlobalToggle";
import { KbDocIde } from "./KbDocIde";
import { useCollectionDocs } from "./useCollectionDocs";
import { useKbOutlet } from "./KbHome";
import { QualityRubricEditor } from "./QualityRubricEditor";
import { RetrievalToggles } from "./RetrievalToggles";
import { WikiBrowser } from "./WikiBrowser";
import { pxToRem } from "../../lib/pxToRem";

// Each tab's name + one-line "what + when" blurb, shown together in the
// collapsible orientation strip under the tabs (#173). A first-timer sees all
// three at once instead of only the active tab (#162's per-tab blurb hid the
// rest). No system nouns (chunk / embed / index internals) — describe the
// outcome. Keyed into the i18n catalog so the strip is bilingual.
const TAB_HELP: Record<
  "documents" | "cards" | "wiki" | "review",
  { label: MsgKey; blurb: MsgKey }
> = {
  documents: { label: "kb.tab.documents", blurb: "kb.tab.documents.blurb" },
  cards: { label: "kb.tab.cards", blurb: "kb.tab.cards.blurb" },
  wiki: { label: "kb.tab.wiki", blurb: "kb.tab.wiki.blurb" },
  review: { label: "kb.tab.review", blurb: "kb.tab.review.blurb" },
};

/** What the collection layout shares with its routed tab children: the open
 * collection, the API client, and the shell's doc-viewer openers (re-provided
 * because a nested Outlet context shadows the shell's). */
export type KbCollectionCtx = {
  collection: KbCollection;
  client: KbApi;
  openDoc: (documentId: string) => void;
  openCite: (c: KbCitation) => void;
  // #172: the Documents tab's empty state offers an upload CTA — opening the
  // file picker is owned by the page (which holds the inputs + mutation).
  onPickFiles?: () => void;
  uploading?: boolean;
};
export function useCollectionOutlet(): KbCollectionCtx {
  return useOutletContext<KbCollectionCtx>();
}

// A DialogProvider wraps the body so the re-index confirm prompts (and any
// future confirms on this page or its tab Outlet) can use the shared modal —
// the same one the file-tree bulk re-index uses, so all three feel identical.
export function KbCollectionPage(props: { client?: KbApi }) {
  return (
    <DialogProvider>
      <KbCollectionPageBody {...props} />
    </DialogProvider>
  );
}

function KbCollectionPageBody({ client = kbApi }: { client?: KbApi }) {
  const t = useT();
  const dialog = useDialog();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { openDoc, openCite } = useKbOutlet();
  // The open collection is the URL (#93): /kb/collections/:cid.
  const { cid } = useParams();
  const { pathname } = useLocation();
  // The "what's in here" orientation strip (#173) defaults open and is
  // collapsed once the reader has the gist — persisted across collections.
  const [overviewCollapsed, setOverviewCollapsed] = usePersistentBoolean(
    "kb:col-overview-collapsed",
    false,
  );
  const [showRetrieval, setShowRetrieval] = useState(false);
  // #355: the code-collection git-connection editor (branch / rotate token).
  const [showGitEdit, setShowGitEdit] = useState(false);
  // The failure list is a default-closed disclosure (#224): the count is always
  // visible, the per-doc rows + retry stay tucked away until expanded. Transient
  // (not persisted) — a failed run is short-lived, so we don't remember it.
  const [failsOpen, setFailsOpen] = useState(false);
  const [iconOpen, setIconOpen] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [editingDesc, setEditingDesc] = useState(false);
  const [descDraft, setDescDraft] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);
  // #310: the share/permission dialog (owner-only). The current access state is
  // fetched lazily when it opens; Save PUTs the full desired state.
  const me = useCurrentUser();
  const [sharing, setSharing] = useState(false);
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

  // Open collection's doc statuses, for the index-status strip (#162/#395).
  // Shares the same queries (keys + fetchers) KbDocIde uses, so the two dedupe;
  // this observer keeps the strip live even on the Cards/Wiki tabs where
  // KbDocIde isn't mounted. The 1.5s tick is the few-hundred-byte status
  // summary — the full list is fetched once and refetched only when the
  // summary's stamp moves, so the old poll-the-whole-collection-per-tick
  // behaviour is gone on every tab.
  const { docs: statusDocs, indexingCount, watchForQueuedWork } = useCollectionDocs(
    cid ?? "__none__",
    client,
    { enabled: cid != null },
  );
  const erroredDocs = statusDocs.filter((d) => d.status === "error");
  const erroredCount = erroredDocs.length;

  // #325: browser-runnable upload checks (fetched once; rarely changes). Used to
  // pre-block encrypted/unreadable files before upload. `blocked` accumulates
  // both pre-blocked files and any the server refuses with a 422; it persists
  // until the user dismisses it (there's no doc to open).
  const uploadHints = useQuery({
    queryKey: qk.kb.uploadChecks,
    queryFn: () => client.listUploadChecks(),
    staleTime: Number.POSITIVE_INFINITY,
  }).data ?? [];
  const [blocked, setBlocked] = useState<BlockedUpload[]>([]);

  // Reset the transient UI when switching to a different collection. (The doc
  // tree + editor own their own state inside KbDocIde.)
  useEffect(() => {
    setIconOpen(false);
    setEditingName(false);
    setEditingDesc(false);
    setConfirmDel(false);
    setShowRetrieval(false);
    setShowGitEdit(false);
    setFailsOpen(false);
    setImportFile(null);
    setBlocked([]);
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

  // #310: the collection's current access state — fetched only while the share
  // dialog is open, so the pre-fill is fresh each time it's reopened.
  const permQuery = useQuery({
    queryKey: qk.kb.collectionPermission(cid ?? ""),
    queryFn: () => client.getCollectionPermission(cid as string),
    enabled: sharing && !!cid,
  });
  const setPermMut = useMutation({
    mutationFn: (perm: CollectionPermission) =>
      client.setCollectionPermission(cid as string, perm),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
      void qc.invalidateQueries({ queryKey: qk.kb.collectionPermission(cid ?? "") });
      setSharing(false);
    },
  });

  const deleteMut = useMutation({
    mutationFn: () => client.deleteCollection(cid as string),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
      navigate("/kb/collections");
    },
  });

  // #569: the re-read is ACCEPTED here, then walked by a worker — so when this
  // resolves the page still looks exactly as it did. Acknowledge explicitly (a
  // dialog, not a fading toast) or the silence reads as "nothing happened" and
  // the user sends the whole collection through again. `queued: false` means the
  // backend coalesced this press onto a run already in flight; say so instead of
  // confirming a send that didn't happen.
  // A send that fails must not be the one silent path left: without this the
  // button simply re-enables and the user is back to guessing (a 403 from
  // `edit_content` and a 500 look identical to "nothing happened").
  const ackReindexFailure = async () => {
    await dialog.confirm({
      title: t("kb.reindexAll.failed"),
      body: t("kb.reindexAll.failedBody"),
      actions: [{ id: "ok", label: t("kb.reindexAll.ack"), variant: "primary" }],
    });
  };

  const ackReindex = async (r: ReindexQueued) => {
    await dialog.confirm({
      title: r.queued ? t("kb.reindexAll.sent") : t("kb.reindexAll.already"),
      body: t(r.queued ? "kb.reindexAll.sentBody" : "kb.reindexAll.alreadyBody", {
        n: r.documents,
      }),
      actions: [{ id: "ok", label: t("kb.reindexAll.ack"), variant: "primary" }],
    });
  };

  const reindexAllMut = useMutation({
    mutationFn: () => client.reindexCollection(cid as string),
    onSuccess: (r) => {
      if (cid) {
        void qc.invalidateQueries({ queryKey: qk.kb.documents(cid) });
        void qc.invalidateQueries({ queryKey: qk.kb.documentsStatus(cid) });
      }
      // Invalidating is NOT enough on its own: the docs no longer flip to
      // `indexing` inside the request, so the one refetch it buys still sees a
      // quiet collection and the 1.5s poll never engages. Arm the watch window
      // so the progress strip appears while the worker gets going.
      if (r.queued) watchForQueuedWork();
      void ackReindex(r);
    },
    onError: () => void ackReindexFailure(),
  });

  // #223: recover only the failed docs (the failure strip's one-click retry),
  // leaving healthy `ready` docs untouched so an outage costs no re-embedding.
  const reindexFailedMut = useMutation({
    mutationFn: () => client.reindexCollection(cid as string, { only: "failed" }),
    onSuccess: (r) => {
      if (cid) {
        void qc.invalidateQueries({ queryKey: qk.kb.documents(cid) });
        // #395: reopen the summary poll gate — the retried docs are indexing.
        void qc.invalidateQueries({ queryKey: qk.kb.documentsStatus(cid) });
      }
      if (r.queued) watchForQueuedWork();  // #569: same delay before anything looks busy
      void ackReindex(r);  // #569: same accept-and-return endpoint, same silence
    },
    onError: () => void ackReindexFailure(),
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

  // Live "uploaded N of M" progress (#170) — a folder pick can be hundreds of
  // files, so a bare "Uploading…" hides how far along we are. Each file that
  // settles bumps `done`; cleared once the whole batch finishes.
  const [upProg, setUpProg] = useState<{ done: number; total: number } | null>(null);

  const uploadMut = useMutation({
    // Bounded concurrency: a folder pick can be hundreds of files — firing them
    // all at once froze the tab and flushed nothing. A small pool keeps the UI
    // alive while still uploading everything.
    mutationFn: async (vars: { files: File[]; asFolder: boolean }) => {
      // #325: pre-block encrypted/unreadable files in the browser (no upload).
      // The retried-now names drop out of the existing blocked list.
      const { allowed, blocked: pre } = await screenFiles(vars.files, uploadHints);
      const retried = new Set(allowed.map((f) => f.name));
      const extra: BlockedUpload[] = pre.map((b) => ({ name: b.file.name, messageKey: b.messageKey }));
      setBlocked((prev) => mergeBlocked(prev.filter((b) => !retried.has(b.name)), extra));

      setUpProg({ done: 0, total: allowed.length });
      let done = 0;
      const beBlocked: BlockedUpload[] = [];
      const ids = await mapWithConcurrency(allowed, 4, async (file) => {
        try {
          const r = await client.uploadDocument(cid as string, file, uploadDocPath(file, vars.asFolder));
          return r;
        } catch (e) {
          // The server refused it (a check the browser couldn't run, e.g. an
          // encrypted PDF) — list it instead of failing the whole batch.
          if (e instanceof UploadBlockedError) {
            beBlocked.push({ name: file.name, messageKey: e.messageKey });
            return [] as string[];
          }
          throw e;
        } finally {
          done += 1;
          setUpProg({ done, total: allowed.length });
        }
      });
      if (beBlocked.length) setBlocked((prev) => mergeBlocked(prev, beBlocked));
      return ids;
    },
    onSuccess: () => {
      if (cid) void qc.invalidateQueries({ queryKey: qk.kb.documents(cid) });
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
    },
    onSettled: () => setUpProg(null),
  });

  const busy = uploadMut.isPending;

  // Drag-and-drop upload over the Documents pane (#172). A depth counter rides
  // out dragenter/dragleave firing on child nodes so the overlay doesn't flicker
  // as the cursor crosses the tree/editor.
  const dragDepth = useRef(0);
  const [dragging, setDragging] = useState(false);

  // "All set" confirmation (#170): without it, the strip just vanishes when the
  // last doc finishes and the user can't tell uploading→indexing→done ever
  // completed. Flash a ✓ only on a real >0→0 transition with no failures (an
  // already-indexed collection that opens clean must NOT flash), then fade.
  const pending = busy || indexingCount > 0;
  const [justReady, setJustReady] = useState(false);
  const hadPending = useRef(false);
  useEffect(() => {
    if (pending) {
      hadPending.current = true;
      setJustReady(false);
      return;
    }
    if (hadPending.current) {
      hadPending.current = false;
      if (erroredCount === 0) {
        setJustReady(true);
        const tmr = setTimeout(() => setJustReady(false), 4000);
        return () => clearTimeout(tmr);
      }
    }
  }, [pending, erroredCount]);

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
  // exists for collections that build one; the 待審核 tab (#415, right of Wiki)
  // is always present — it holds generated card proposals awaiting review.
  const tabIds = (
    selected.use_wiki
      ? ["documents", "cards", "wiki", "review"]
      : ["documents", "cards", "review"]
  ) as ("documents" | "cards" | "wiki" | "review")[];
  // Which tab is open (the URL is the source of truth, #93) — drives the
  // Documents-only affordances (Re-index action + drag-drop upload, #172).
  const activeTab: "documents" | "cards" | "wiki" | "review" = pathname.endsWith("/cards")
    ? "cards"
    : pathname.endsWith("/wiki")
      ? "wiki"
      : pathname.endsWith("/review")
        ? "review"
        : "documents";

  // Re-indexing every document (or every failed one) restarts a lot of work, so
  // gate the whole-collection actions behind a confirm — same modal the file
  // tree uses for a >=2 bulk re-index (KbDocIde), so the three feel identical.
  const askReindexAll = async () => {
    if (!selected || selected.doc_count === 0) return;
    const n = selected.doc_count;
    const choice = await dialog.confirm({
      title: "Re-read all documents",
      body: `Re-read all ${n} ${n === 1 ? "document" : "documents"} in “${selected.name}”? The AI reads each one again from scratch.`,
      actions: [
        { id: "go", label: t("kb.reindexAll"), variant: "primary" },
        { id: "cancel", label: "Cancel" },
      ],
    });
    if (choice === "go") reindexAllMut.mutate();
  };
  const askReindexFailed = async () => {
    const n = erroredCount;
    const choice = await dialog.confirm({
      title: "Re-read failed documents",
      body: `Re-read ${n} failed ${n === 1 ? "document" : "documents"}?`,
      actions: [
        { id: "go", label: t("kb.status.retryFailed"), variant: "primary" },
        { id: "cancel", label: "Cancel" },
      ],
    });
    if (choice === "go") reindexFailedMut.mutate();
  };

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
                className={`kb-chip kb-chip--btn${isPinned ? " is-on" : ""}`}
                aria-label={`${isPinned ? "Unpin" : "Pin"} ${selected.name}`}
                aria-pressed={isPinned}
                onClick={() => pinned.toggle(selected.resource_id)}
              >
                <Icon name="pin" size={10} /> {isPinned ? "Pinned" : "Pin"}
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
              <div className="kb-colpage__titlerow">
                <h1
                  className="kb-colpage__title"
                  title="Rename"
                  onClick={() => {
                    setNameDraft(selected.name);
                    setEditingName(true);
                  }}
                >
                  {selected.name}
                </h1>
                <button
                  type="button"
                  className="kb-colpage__editbtn"
                  aria-label={`Rename ${selected.name}`}
                  title="Rename"
                  onClick={() => {
                    setNameDraft(selected.name);
                    setEditingName(true);
                  }}
                >
                  <Icon name="pencil" size={14} />
                </button>
              </div>
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
                {selected.git_url ? (
                  <button type="button" role="menuitem" className="kb-menu__item" onClick={() => { close(); setShowGitEdit((v) => !v); }}>
                    <Icon name="git" size={14} color="var(--text-paper-d)" /> Git connection
                  </button>
                ) : null}
                <button type="button" role="menuitem" className="kb-menu__item" disabled={selected.doc_count === 0 || reindexAllMut.isPending} onClick={() => { close(); void askReindexAll(); }}>
                  <Icon name="refresh" size={14} color="var(--text-paper-d)" /> {t("kb.reindexAll")}
                </button>
                <button type="button" role="menuitem" className="kb-menu__item" disabled={downloadMut.isPending} onClick={() => { close(); downloadMut.mutate(selected.resource_id); }}>
                  <Icon name="download" size={14} color="var(--text-paper-d)" /> Download collection
                </button>
                <button type="button" role="menuitem" className="kb-menu__item" onClick={() => { close(); importIntoRef.current?.click(); }}>
                  <Icon name="upload" size={14} color="var(--text-paper-d)" /> Import into this collection
                </button>
                {selected.owner === me ? (
                  <button type="button" role="menuitem" className="kb-menu__item" data-testid="manage-access" onClick={() => { close(); setSharing(true); }}>
                    <Icon name="users" size={14} color="var(--text-paper-d)" /> Manage access
                  </button>
                ) : null}
                <div className="kb-menu__divider" />
                <button type="button" role="menuitem" className="kb-menu__item kb-menu__item--danger" onClick={() => { close(); setConfirmDel(true); }}>
                  <Icon name="x" size={14} /> Delete collection
                </button>
              </div>
            )}
          </Popover>

          {/* Upload is the collection's most-used action — two one-click
              buttons instead of a dropdown that hides it behind a menu (#172). */}
          <button
            type="button"
            className="kb-btn kb-btn--primary"
            disabled={busy}
            onClick={() => fileRef.current?.click()}
          >
            <Icon name="file" size={13} /> {t("kb.uploadFiles")}
          </button>
          <button
            type="button"
            className="kb-btn"
            disabled={busy}
            onClick={() => folderRef.current?.click()}
          >
            <Icon name="folder" size={13} /> {t("kb.uploadFolder")}
          </button>

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

          {sharing && permQuery.data && (
            <PermissionDialog
              resourceName={selected.name}
              owner={selected.owner}
              value={permQuery.data}
              busy={setPermMut.isPending}
              onSubmit={(perm) => setPermMut.mutate(perm)}
              onClose={() => setSharing(false)}
            />
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

      {/* #355: code-collection sync strip — clone/ingest/build progress, the
          synced commit, and a Sync now / Retry action. Only for code collections. */}
      {selected.git_url ? <CodeSyncStatus collection={selected} client={client} /> : null}

      {/* Index-status strip (#162, #170): visible on every tab so the upload →
          indexing → ready/error transition is never invisible. Shows live
          progress, then either a transient "all set" ✓ or a persistent failure
          list. Hidden only when idle with nothing to report. */}
      {(pending || erroredCount > 0 || justReady || blocked.length > 0) && (
        <div
          className={`kb-index-status${(erroredCount > 0 && !pending) || blocked.length > 0 ? " is-error" : ""}`}
          data-testid="kb-index-status"
          role="status"
        >
          {/* #325: files refused at upload (encrypted/unreadable). No doc to
              open — just the reason + "decrypt and re-upload", dismissible. */}
          <UploadBlockedList items={blocked} onDismiss={() => setBlocked([])} />
          {/* Live progress while work is in flight (#170). De-jargoned wording
              follows #171 (processing, not indexing). */}
          {pending && (
            <div className="kb-index-status__line">
              <Icon name="refresh" size={13} color="var(--accent-h)" />
              <span>
                {[
                  busy
                    ? t("kb.status.uploadingProgress", { done: upProg?.done ?? 0, total: upProg?.total ?? 0 })
                    : null,
                  indexingCount > 0 ? t("kb.status.indexing", { n: indexingCount }) : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </div>
          )}

          {/* Transient "all set" ✓ — only on a clean finish (#170). */}
          {justReady && !pending && erroredCount === 0 && (
            <span className="kb-index-status__ready">{t("kb.status.allReady")}</span>
          )}

          {/* Persistent failure list (#170), tucked behind a default-closed
              disclosure (#224): the count stays visible as the trigger, while
              the per-doc rows + retry are revealed on demand. Each failed doc is
              named by reason, click to open it (the viewer shows the full
              status_detail). */}
          {erroredCount > 0 && (
            <div className="kb-index-status__fails">
              <button
                type="button"
                className="kb-index-status__fails-toggle"
                aria-expanded={failsOpen}
                aria-controls="kb-index-fails-panel"
                onClick={() => setFailsOpen((v) => !v)}
              >
                <Icon name="x" size={13} color="var(--err)" />
                <span>{t("kb.status.failed", { n: erroredCount })}</span>
                <Icon name={failsOpen ? "chev_d" : "chev_r"} size={11} />
              </button>
              {failsOpen && (
                <div id="kb-index-fails-panel" className="kb-index-status__fails-panel">
                  {/* #223: re-queue ONLY the failed docs in one click — recover
                      after a transient outage without re-embedding the rest. */}
                  <button
                    type="button"
                    className="kb-btn kb-index-status__retry"
                    data-testid="kb-reindex-failed"
                    disabled={reindexFailedMut.isPending}
                    onClick={() => void askReindexFailed()}
                  >
                    <Icon name="refresh" size={12} /> {t("kb.status.retryFailed")}
                  </button>
                  <ul className="kb-index-status__fail-list">
                    {erroredDocs.map((d) => {
                      const name = d.path.split("/").pop() ?? d.path;
                      return (
                        <li key={d.resource_id}>
                          <button
                            type="button"
                            className="kb-index-status__fail"
                            aria-label={t("kb.status.openFailed", { name })}
                            onClick={() => openDoc(d.resource_id)}
                          >
                            <span className="kb-index-status__fail-name">{name}</span>
                            <span className="kb-index-status__fail-reason">
                              {d.status_detail || t("kb.doc.processingFailed")}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {showGitEdit && selected.git_url ? (
        <CodeConnectionEditor
          collection={selected}
          client={client}
          onClose={() => setShowGitEdit(false)}
        />
      ) : null}

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
          {/* #105: the quality rubric lives with the other search-ranking
              settings — it shapes how docs are scored + down-weighted. */}
          <div style={{ marginTop: 12 }}>
            <QualityRubricEditor
              collectionId={selected.resource_id}
              rubric={selected.quality_rubric ?? ""}
              client={client}
            />
          </div>
          <div style={{ height: 1, background: "var(--paper-3)", margin: "12px 0" }} />
          {/* #377: auto-generate cards per indexed doc (user-owned setting). */}
          <CardGenToggle collection={selected} client={client} />
          {/* Global collection = part of every AI chat's baseline retrieval
              scope, so it belongs with "how answers are found". Superuser-only —
              renders nothing for others (tucked in settings, not the bare header). */}
          <div style={{ marginTop: 10 }}>
            <GlobalToggle collection={selected} client={client} />
          </div>
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
            {t(TAB_HELP[id].label)}
          </NavLink>
        ))}
      </div>

      {/* "What's in here" orientation strip (#173): all tab blurbs at once so a
          first-timer never has to click each tab to learn what it is. Defaults
          open, collapses to a single re-expand affordance once dismissed. */}
      <div className="kb-tabs__orient">
        <button
          type="button"
          className="kb-tabs__orient-toggle"
          aria-expanded={!overviewCollapsed}
          onClick={() => setOverviewCollapsed((v) => !v)}
        >
          <Icon name={overviewCollapsed ? "chev_r" : "chev_d"} size={11} />
          {overviewCollapsed ? t("kb.col.overview.expand") : t("kb.col.overview.title")}
        </button>
        {!overviewCollapsed && (
          <ul className="kb-tabs__orient-list">
            {tabIds.map((id) => (
              <li key={id} className="kb-tabs__orient-item">
                <span className="kb-tabs__orient-label">{t(TAB_HELP[id].label)}</span>
                <span className="kb-tabs__orient-blurb">{t(TAB_HELP[id].blurb)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Re-index all also lives in the ⚙ menu, but it's a documents action —
          surface it on the Documents tab too so it's discoverable (#172). */}
      {activeTab === "documents" && (
        <div style={{ display: "flex", justifyContent: "flex-end", padding: "0 0 8px" }}>
          <button
            type="button"
            className="kb-btn"
            data-testid="kb-reindex-all"
            disabled={selected.doc_count === 0 || reindexAllMut.isPending}
            onClick={() => void askReindexAll()}
          >
            <Icon name="refresh" size={12} /> {t("kb.reindexAll")}
          </button>
        </div>
      )}

      <div
        className="kb-colpage__docs"
        data-testid="kb-docs-dropzone"
        style={{ position: "relative" }}
        {...(activeTab === "documents"
          ? {
              onDragEnter: (e: React.DragEvent) => {
                e.preventDefault();
                dragDepth.current += 1;
                setDragging(true);
              },
              onDragOver: (e: React.DragEvent) => e.preventDefault(),
              onDragLeave: (e: React.DragEvent) => {
                e.preventDefault();
                dragDepth.current -= 1;
                if (dragDepth.current <= 0) {
                  dragDepth.current = 0;
                  setDragging(false);
                }
              },
              onDrop: (e: React.DragEvent) => {
                e.preventDefault();
                dragDepth.current = 0;
                setDragging(false);
                upload(e.dataTransfer?.files ?? null);
              },
            }
          : {})}
      >
        <Outlet
          context={
            {
              collection: selected,
              client,
              openDoc,
              openCite,
              onPickFiles: () => fileRef.current?.click(),
              uploading: busy,
            } satisfies KbCollectionCtx
          }
        />
        {dragging && (
          <div
            data-testid="kb-drop-overlay"
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
              border: "2px dashed var(--accent)",
              borderRadius: 8,
              background: "color-mix(in srgb, var(--accent) 10%, var(--white))",
              color: "var(--accent-h)",
              fontSize: pxToRem(14),
              fontWeight: 600,
              pointerEvents: "none",
            }}
          >
            <Icon name="upload" size={16} color="var(--accent-h)" /> {t("kb.dropToUpload")}
          </div>
        )}
      </div>
    </section>
  );
}

// ---- the tab routes (#93): each renders one tab's content from the layout's
// Outlet context, so the active tab is the URL, not component state. ----

export function DocumentsTab() {
  // Documents as a VSCode-shaped tree + editor (#87) — the same shell the
  // investigation workspace uses, over this collection's docs.
  const { collection, client, onPickFiles, uploading } = useCollectionOutlet();
  return (
    <KbDocIde
      collectionId={collection.resource_id}
      client={client}
      {...(onPickFiles ? { onPickFiles } : {})}
      {...(uploading != null ? { uploading } : {})}
    />
  );
}

export function CardsTab() {
  const { collection, client } = useCollectionOutlet();
  return <ContextCardsTab collectionId={collection.resource_id} client={client} />;
}

export function ReviewTab() {
  const { collection, client } = useCollectionOutlet();
  return <CollectionReviewTab collectionId={collection.resource_id} client={client} />;
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
      isCodeWiki={!!collection.git_url}
      lastReflectedAt={collection.last_reflected_at ?? ""}
    />
  );
}
