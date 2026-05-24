/**
 * KB management — collections on the left, the selected collection's documents
 * on the right with upload. Maps the design's "sources" surface onto the real
 * backend (named collections of uploaded md/txt/archives); prototype-only
 * features (sync/owners/sharing/source types) are intentionally dropped.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { kbApi, type KbApi, type KbCollection, type KbDocument } from "../../api/kb";
import { Icon } from "../../components/Icon";

export function KbCollectionsPage({
  client = kbApi,
  onOpenDoc,
}: {
  client?: KbApi;
  onOpenDoc?: (documentId: string) => void;
}) {
  const [collections, setCollections] = useState<KbCollection[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<KbDocument[]>([]);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refreshCollections = useCallback(async () => {
    const cols = await client.listCollections();
    setCollections(cols);
    setSelectedId((cur) => cur ?? cols[0]?.resource_id ?? null);
  }, [client]);

  useEffect(() => {
    void refreshCollections();
  }, [refreshCollections]);

  useEffect(() => {
    let mounted = true;
    if (selectedId == null) {
      setDocuments([]);
      return;
    }
    client.listDocuments(selectedId).then((d) => mounted && setDocuments(d));
    return () => {
      mounted = false;
    };
  }, [selectedId, client]);

  const createCollection = async () => {
    const name = newName.trim();
    if (!name || busy) return;
    setBusy(true);
    try {
      const c = await client.createCollection(name);
      setNewName("");
      await refreshCollections();
      setSelectedId(c.resource_id);
    } finally {
      setBusy(false);
    }
  };

  const upload = async (files: FileList | null) => {
    if (!files || !selectedId || busy) return;
    setBusy(true);
    try {
      for (const file of Array.from(files)) {
        await client.uploadDocument(selectedId, file);
      }
      setDocuments(await client.listDocuments(selectedId));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const selected = collections.find((c) => c.resource_id === selectedId) ?? null;

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
              <input
                ref={fileRef}
                type="file"
                multiple
                accept=".md,.txt,.zip,.tar,.gz,.tgz"
                hidden
                onChange={(e) => upload(e.target.files)}
              />
            </header>
            {documents.length === 0 ? (
              <p className="kb-cols__empty">Upload markdown, text, or an archive to index it.</p>
            ) : (
              <ul className="kb-docs__rows">
                {documents.map((d) => (
                  <li key={d.resource_id}>
                    <button
                      type="button"
                      className="kb-docs__row"
                      onClick={() => onOpenDoc?.(d.resource_id)}
                    >
                      <Icon name="file" size={14} color="var(--text-paper-d)" />
                      <span className="kb-docs__path">{d.path}</span>
                      <span className="kb-docs__type">{d.content_type}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p className="kb-cols__empty">Create a collection to start adding documents.</p>
        )}
      </section>
    </div>
  );
}
