/**
 * KB management — a grid of collection cards (icon, description, doc/size/cited
 * chips, owner, last-updated; pinnable), matching the design handoff. Clicking a
 * card opens that collection's documents (upload + per-doc size/chunks/cited).
 * Prototype-only bits we don't model (org/team sharing, auto-managed) are
 * dropped; pinned is local (localStorage).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { kbApi, type KbApi, type KbDocument } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon, type IconName } from "../../components/Icon";
import { UserAvatar } from "../../components/UserChip";
import { usePersistentSet } from "../../hooks/usePersistentSet";
import { docHref } from "./kbLinks";

/** Compact byte size: B / KB / MB, rounded to whole units. */
function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${Math.round(n / (1024 * 1024))} MB`;
}

/** Short "MMM D" update date. */
function fmtDate(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function KbCollectionsPage({
  client = kbApi,
  onOpenDoc,
}: {
  client?: KbApi;
  onOpenDoc?: (documentId: string) => void;
}) {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [docQuery, setDocQuery] = useState("");
  const [newName, setNewName] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const pinned = usePersistentSet("kb:pinned-collections");

  // webkitdirectory isn't a typed JSX attribute, and the folder input only
  // mounts on the docs view — so set it via a callback ref that fires on every
  // (re)mount, not a one-shot effect.
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

  useEffect(() => setDocQuery(""), [selectedId]);

  const { data: documents = [] } = useQuery({
    queryKey: qk.kb.documents(selectedId ?? "__none__"),
    queryFn: () => client.listDocuments(selectedId as string),
    enabled: selectedId != null,
    // While any doc is still indexing, re-poll so its chip flips to "ready".
    refetchInterval: (query) => {
      const data = query.state.data as KbDocument[] | undefined;
      return data?.some((d) => d.status === "indexing") ? 1500 : false;
    },
  });

  const createMut = useMutation({
    mutationFn: (name: string) => client.createCollection(name),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
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

  const busy = createMut.isPending || uploadMut.isPending;

  const createCollection = () => {
    const name = newName.trim();
    if (!name || busy) return;
    setNewName("");
    createMut.mutate(name);
  };

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
  // Pinned collections float to the top, then alphabetical.
  const sorted = [...collections].sort(
    (a, b) =>
      Number(pinned.has(b.resource_id)) - Number(pinned.has(a.resource_id)) ||
      a.name.localeCompare(b.name),
  );
  const q = docQuery.trim().toLowerCase();
  const shownDocs = q ? documents.filter((d) => d.path.toLowerCase().includes(q)) : documents;

  // ---- documents view (a collection is open) ----
  if (selected) {
    return (
      <section className="kb-docs" aria-label="Documents">
        <header className="kb-docs__head">
          <button type="button" className="kb-nav__back" onClick={() => setSelectedId(null)}>
            <Icon name="chev_l" size={13} /> Collections
          </button>
          <div>
            <h2 className="kb-docs__title">{selected.name}</h2>
            <p className="kb-docs__sub">
              {documents.length} {documents.length === 1 ? "document" : "documents"}
            </p>
          </div>
          <button type="button" className="kb-btn" disabled={busy} onClick={() => fileRef.current?.click()}>
            <Icon name="upload" size={13} /> Upload
          </button>
          <button type="button" className="kb-btn" disabled={busy} onClick={() => folderRef.current?.click()}>
            <Icon name="folder" size={13} /> Upload folder
          </button>
          <input ref={fileRef} type="file" multiple accept=".md,.txt,.zip,.tar,.gz,.tgz" hidden onChange={(e) => upload(e.target.files)} />
          <input ref={folderInputRef} type="file" multiple hidden onChange={(e) => upload(e.target.files, true)} />
        </header>
        {documents.length === 0 ? (
          <p className="kb-cols__empty">Upload markdown, text, or an archive to index it.</p>
        ) : (
          <>
            <label className="kb-docsearch">
              <Icon name="search" size={14} color="var(--text-paper-d)" />
              <input type="search" placeholder="Filter documents by name…" value={docQuery} onChange={(e) => setDocQuery(e.target.value)} />
            </label>
            {shownDocs.length === 0 ? (
              <p className="kb-cols__empty">No documents match “{docQuery}”.</p>
            ) : (
              <ul className="kb-docs__rows">
                {shownDocs.map((d) => (
                  <li key={d.resource_id} className="kb-docs__row">
                    <button type="button" className="kb-docs__open" onClick={() => onOpenDoc?.(d.resource_id)}>
                      <Icon name="file" size={14} color="var(--text-paper-d)" />
                      <span className="kb-docs__path">{d.path}</span>
                    </button>
                    {typeof d.size === "number" && (
                      <span className="kb-docs__metric" title="File size">
                        {fmtBytes(d.size)}
                      </span>
                    )}
                    {typeof d.chunks === "number" && (
                      <span className="kb-docs__metric" title="Indexed chunks">
                        <Icon name="layers" size={11} color="var(--text-paper-d2)" />
                        {d.chunks} chunks
                      </span>
                    )}
                    <span className="kb-docs__metric" title="Times cited">
                      <Icon name="quote" size={11} color="var(--text-paper-d2)" />
                      {d.cited ?? 0} cited
                    </span>
                    {typeof d.updated_at === "number" && (
                      <span className="kb-docs__metric" title="Last updated">
                        <Icon name="clock" size={11} color="var(--text-paper-d2)" />
                        {fmtDate(d.updated_at)}
                      </span>
                    )}
                    <span className="kb-docs__by" title="Added by">
                      <Icon name="user" size={11} color="var(--text-paper-d2)" />
                      {d.created_by}
                    </span>
                    <span className={`kb-status kb-status--${d.status}`}>
                      {d.status === "indexing" ? "indexing…" : d.status === "error" ? "error" : "indexed"}
                    </span>
                    <a className="kb-iconbtn" href={docHref(d.resource_id)} target="_blank" rel="noreferrer" title="Open in new tab" aria-label={`Open ${d.path} in new tab`}>
                      <Icon name="arrow_u" size={14} />
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>
    );
  }

  // ---- collection grid (landing) ----
  return (
    <section className="kb-grid-page" aria-label="Collections">
      <div className="kb-kpis">
        <div className="kb-kpi">
          <span className="kb-kpi__value">{collections.length}</span>
          <span className="kb-kpi__label">Collections</span>
        </div>
        <div className="kb-kpi">
          <span className="kb-kpi__value kb-kpi__value--text" title={mostCited?.name}>
            {mostCited && mostCited.cited > 0 ? mostCited.name : "—"}
          </span>
          <span className="kb-kpi__label">Most cited</span>
        </div>
      </div>

      <div className="kb-cols__create">
        <input
          className="kb-input"
          placeholder="New collection name…"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && createCollection()}
        />
        <button type="button" className="kb-btn kb-btn--primary" disabled={busy || !newName.trim()} onClick={createCollection}>
          <Icon name="plus" size={13} /> New collection
        </button>
      </div>

      {collections.length === 0 ? (
        <p className="kb-cols__empty">No collections yet — create one to start adding documents.</p>
      ) : (
        <div className="kb-grid">
          {sorted.map((c) => (
            <div key={c.resource_id} className="kb-card-wrap">
              <button
                type="button"
                className="kb-card"
                aria-label={`Open ${c.name}`}
                onClick={() => setSelectedId(c.resource_id)}
              >
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
