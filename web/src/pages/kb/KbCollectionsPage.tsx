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

import { kbApi, type KbApi, type KbDocument } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon, type IconName } from "../../components/Icon";
import { Popover } from "../../components/Popover";
import { UserAvatar } from "../../components/UserChip";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { usePersistentSet } from "../../hooks/usePersistentSet";
import { kindIcon } from "./docKind";
import { docHref } from "./kbLinks";
import { NewCollectionModal } from "./NewCollectionModal";

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
  const [docQuery, setDocQuery] = useState("");
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
  const pinned = usePersistentSet("kb:pinned-collections");

  const folderInputRef = (el: HTMLInputElement | null) => {
    folderRef.current = el;
    if (el) {
      el.webkitdirectory = true;
      el.setAttribute("webkitdirectory", "");
    }
  };

  const { data: collections = [] } = useQuery({
    queryKey: qk.kb.collections,
    queryFn: () => client.listCollections(),
  });

  // Reset the open collection's transient UI when switching away.
  useEffect(() => {
    setDocQuery("");
    setIconOpen(false);
    setEditingName(false);
    setEditingDesc(false);
    setConfirmDel(false);
  }, [selectedId]);

  const { data: documents = [] } = useQuery({
    queryKey: qk.kb.documents(selectedId ?? "__none__"),
    queryFn: () => client.listDocuments(selectedId as string),
    enabled: selectedId != null,
    refetchInterval: (query) => {
      const data = query.state.data as KbDocument[] | undefined;
      return data?.some((d) => d.status === "indexing") ? 1500 : false;
    },
  });

  const createMut = useMutation({
    mutationFn: (v: { name: string; description: string }) =>
      client.createCollection(v.name, v.description),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
  });

  const updateMut = useMutation({
    mutationFn: (patch: { name?: string; icon?: string; description?: string }) =>
      client.updateCollection(selectedId as string, patch),
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

  const uploadMut = useMutation({
    mutationFn: (vars: { files: File[]; asFolder: boolean }) =>
      Promise.all(
        vars.files.map((file) =>
          client.uploadDocument(
            selectedId as string,
            file,
            vars.asFolder ? file.webkitRelativePath : undefined,
          ),
        ),
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
      { onSettled: () => fileRef.current && (fileRef.current.value = "") },
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
  const q = docQuery.trim().toLowerCase();
  const shownDocs = q ? documents.filter((d) => d.path.toLowerCase().includes(q)) : documents;

  // ---- the full collection page (a collection is open) ----
  if (selected) {
    const isPinned = pinned.has(selected.resource_id);
    const chunksTotal = documents.reduce((s, d) => s + (d.chunks ?? 0), 0);
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
      ["Chunks", String(chunksTotal)],
      ["Cited", `${selected.cited}×`, selected.cited > 0],
      ["Owner", selected.owner],
      ["Updated", fmtDate(selected.updated_at)],
    ];
    return (
      <section className="kb-colpage" aria-label="Collection">
        <input ref={fileRef} type="file" multiple accept=".md,.txt,.zip,.tar,.gz,.tgz" hidden onChange={(e) => upload(e.target.files)} />
        <input ref={folderInputRef} type="file" multiple hidden onChange={(e) => upload(e.target.files, true)} />

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
                  <button type="button" role="menuitem" className="kb-menu__item" disabled={documents.length === 0 || reindexAllMut.isPending} onClick={() => { close(); reindexAllMut.mutate(); }}>
                    <Icon name="refresh" size={14} color="var(--text-paper-d)" /> Re-index all
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
                <button type="button" className="kb-btn kb-btn--danger" onClick={() => deleteMut.mutate()}>
                  Delete
                </button>
                <button type="button" className="kb-btn" onClick={() => setConfirmDel(false)}>
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

        <div className="kb-colpage__docs">
          {documents.length === 0 ? (
            <p className="kb-cols__empty">Upload markdown, text, or an archive to index it.</p>
          ) : (
            <>
              <label className="kb-docsearch">
                <Icon name="search" size={14} color="var(--text-paper-d)" />
                <input type="search" placeholder="Search in this collection…" value={docQuery} onChange={(e) => setDocQuery(e.target.value)} />
              </label>
              {shownDocs.length === 0 ? (
                <p className="kb-cols__empty">No documents match “{docQuery}”.</p>
              ) : (
                <div className="kb-doctable">
                  <div className="kb-doctable__head">
                    <span />
                    <span className="kb-doctable__h">Name</span>
                    <span className="kb-doctable__h">Uploaded by</span>
                    <span className="kb-doctable__h">Updated</span>
                    <span className="kb-doctable__h kb-doctable__num">Size</span>
                    <span className="kb-doctable__h kb-doctable__num">Chunks</span>
                    <span className="kb-doctable__h kb-doctable__num">Cited</span>
                    <span />
                  </div>
                  {shownDocs.map((d) => (
                    <div key={d.resource_id} className="kb-doctable__row">
                      <span className="kb-doctable__kind">
                        <Icon name={kindIcon(d.path)} size={14} color="var(--text-paper-d)" />
                      </span>
                      <button type="button" className="kb-doctable__name" onClick={() => onOpenDoc?.(d.resource_id)}>
                        {d.path}
                      </button>
                      <span className="kb-doctable__by" title="Added by">
                        <UserAvatar userId={d.created_by} size={20} />
                        {d.created_by}
                      </span>
                      <span className="kb-doctable__cell mono">
                        {typeof d.updated_at === "number" ? fmtDate(d.updated_at) : "—"}
                      </span>
                      <span className="kb-doctable__cell mono kb-doctable__num">
                        {typeof d.size === "number" ? fmtBytes(d.size) : "—"}
                      </span>
                      <span className="kb-doctable__cell mono kb-doctable__num">{d.chunks ?? "—"}</span>
                      <span className={`kb-doctable__cell mono kb-doctable__num${(d.cited ?? 0) > 0 ? " is-hot" : ""}`}>
                        {d.cited ?? 0}
                      </span>
                      <span className="kb-doctable__actions">
                        {d.status !== "ready" && (
                          <span className={`kb-status kb-status--${d.status}`}>
                            {d.status === "indexing" ? "indexing…" : "error"}
                          </span>
                        )}
                        <a className="kb-iconbtn" href={docHref(d.resource_id)} target="_blank" rel="noreferrer" title="Open in new tab" aria-label={`Open ${d.path} in new tab`}>
                          <Icon name="arrow_u" size={13} />
                        </a>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
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
        <button type="button" className="kb-btn kb-btn--primary" onClick={() => setNewOpen(true)}>
          <Icon name="plus" size={13} /> New collection
        </button>
      </div>

      <NewCollectionModal
        open={newOpen}
        busy={createMut.isPending}
        onClose={() => setNewOpen(false)}
        onCreate={(name, description) =>
          createMut.mutate({ name, description }, { onSuccess: () => setNewOpen(false) })
        }
      />

      {collections.length === 0 ? (
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
