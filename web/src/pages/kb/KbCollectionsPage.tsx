/**
 * KB management — collections on the left, the selected collection's documents
 * on the right with upload. Maps the design's "sources" surface onto the real
 * backend (named collections of uploaded md/txt/archives); prototype-only
 * features (sync/owners/sharing/source types) are intentionally dropped.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { kbApi, type KbApi, type KbDocument } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { docHref } from "./kbLinks";

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

  // webkitdirectory isn't a typed JSX attribute, and the folder input only
  // mounts once a collection is selected — so set it via a callback ref that
  // fires on every (re)mount, not a one-shot effect.
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

  // Default to the first collection once the list loads.
  useEffect(() => {
    setSelectedId((cur) => cur ?? collections[0]?.resource_id ?? null);
  }, [collections]);
  // Reset the filter when switching collections.
  useEffect(() => setDocQuery(""), [selectedId]);

  const { data: documents = [] } = useQuery({
    queryKey: qk.kb.documents(selectedId ?? "__none__"),
    queryFn: () => client.listDocuments(selectedId as string),
    enabled: selectedId != null,
    // While any doc is still indexing (embedded in the background), re-poll so
    // its chip flips to "ready".
    refetchInterval: (query) => {
      const data = query.state.data as KbDocument[] | undefined;
      return data?.some((d) => d.status === "indexing") ? 1500 : false;
    },
  });

  const createMut = useMutation({
    mutationFn: (name: string) => client.createCollection(name),
    onSuccess: (c) => {
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
      setSelectedId(c.resource_id);
    },
  });

  const uploadMut = useMutation({
    // Folder uploads keep each file's relative path (handled like an archive).
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
      if (selectedId)
        void qc.invalidateQueries({ queryKey: qk.kb.documents(selectedId) });
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
      {
        onSettled: () => {
          if (fileRef.current) fileRef.current.value = "";
        },
      },
    );
  };

  const selected = collections.find((c) => c.resource_id === selectedId) ?? null;
  const q = docQuery.trim().toLowerCase();
  const shownDocs = q ? documents.filter((d) => d.path.toLowerCase().includes(q)) : documents;

  return (
    <div className="kb-cols">
      <section className="kb-cols__list" aria-label="Collections">
        <div className="kb-cols__create">
          <input
            className="kb-input"
            placeholder="New collection name…"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && createCollection()}
          />
          <button
            type="button"
            className="kb-btn kb-btn--primary"
            disabled={busy || !newName.trim()}
            onClick={createCollection}
          >
            <Icon name="plus" size={13} /> Add
          </button>
        </div>
        {collections.length === 0 && <p className="kb-cols__empty">No collections yet.</p>}
        {collections.map((c) => (
          <button
            key={c.resource_id}
            type="button"
            className={`kb-cols__item${c.resource_id === selectedId ? " is-active" : ""}`}
            onClick={() => setSelectedId(c.resource_id)}
          >
            <Icon name="layers" size={15} color="var(--text-paper-d)" />
            <span className="kb-cols__name">{c.name}</span>
          </button>
        ))}
      </section>

      <section className="kb-docs" aria-label="Documents">
        {selected ? (
          <>
            <header className="kb-docs__head">
              <div>
                <h2 className="kb-docs__title">{selected.name}</h2>
                <p className="kb-docs__sub">
                  {documents.length} {documents.length === 1 ? "document" : "documents"}
                </p>
              </div>
              <button
                type="button"
                className="kb-btn"
                disabled={busy}
                onClick={() => fileRef.current?.click()}
              >
                <Icon name="upload" size={13} /> Upload
              </button>
              <button
                type="button"
                className="kb-btn"
                disabled={busy}
                onClick={() => folderRef.current?.click()}
              >
                <Icon name="folder" size={13} /> Upload folder
              </button>
              <input
                ref={fileRef}
                type="file"
                multiple
                accept=".md,.txt,.zip,.tar,.gz,.tgz"
                hidden
                onChange={(e) => upload(e.target.files)}
              />
              <input
                ref={folderInputRef}
                type="file"
                multiple
                hidden
                onChange={(e) => upload(e.target.files, true)}
              />
            </header>
            {documents.length === 0 ? (
              <p className="kb-cols__empty">Upload markdown, text, or an archive to index it.</p>
            ) : (
              <>
                <label className="kb-docsearch">
                  <Icon name="search" size={14} color="var(--text-paper-d)" />
                  <input
                    type="search"
                    placeholder="Filter documents by name…"
                    value={docQuery}
                    onChange={(e) => setDocQuery(e.target.value)}
                  />
                </label>
                {shownDocs.length === 0 ? (
                  <p className="kb-cols__empty">No documents match “{docQuery}”.</p>
                ) : (
                  <ul className="kb-docs__rows">
                    {shownDocs.map((d) => (
                  <li key={d.resource_id} className="kb-docs__row">
                    <button
                      type="button"
                      className="kb-docs__open"
                      onClick={() => onOpenDoc?.(d.resource_id)}
                    >
                      <Icon name="file" size={14} color="var(--text-paper-d)" />
                      <span className="kb-docs__path">{d.path}</span>
                    </button>
                    <span className="kb-docs__by" title="Added by">
                      <Icon name="user" size={11} color="var(--text-paper-d2)" />
                      {d.created_by}
                    </span>
                    <span className={`kb-status kb-status--${d.status}`}>
                      {d.status === "indexing"
                        ? "indexing…"
                        : d.status === "error"
                          ? "error"
                          : "indexed"}
                    </span>
                    <a
                      className="kb-iconbtn"
                      href={docHref(d.resource_id)}
                      target="_blank"
                      rel="noreferrer"
                      title="Open in new tab"
                      aria-label={`Open ${d.path} in new tab`}
                    >
                      <Icon name="arrow_u" size={14} />
                    </a>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </>
        ) : (
          <p className="kb-cols__empty">Create a collection to start adding documents.</p>
        )}
      </section>
    </div>
  );
}
