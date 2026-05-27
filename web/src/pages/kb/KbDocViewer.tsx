/**
 * KB document viewer — a right-hand drawer that renders a source document so a
 * citation (or a collection doc) can be inspected in place. The header carries
 * the doc's metadata (size · cited · chunks · uploaded) and an action bar:
 * open full view, download the original blob, re-index, or remove. kb:// body
 * links swap the viewed document in place.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { kbApi, type KbApi, type KbRenderedDoc } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { kindIcon } from "./docKind";
import { KbDocBody } from "./KbDocBody";
import { blobHref, docHref } from "./kbLinks";

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${Math.round(n / (1024 * 1024))} MB`;
}

function fmtDate(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function KbDocViewer({
  documentId,
  snippet,
  onClose,
  onChanged,
  client = kbApi,
}: {
  documentId: string;
  /** The cited passage text, shown as a callout when opened from a citation. */
  snippet?: string;
  onClose: () => void;
  /** A doc was re-indexed or removed — let the opener refresh its lists. */
  onChanged?: () => void;
  client?: KbApi;
}) {
  const qc = useQueryClient();
  const [docId, setDocId] = useState(documentId);
  const [doc, setDoc] = useState<KbRenderedDoc | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);

  useEffect(() => {
    setDocId(documentId);
    setDoc(null);
    setConfirmRemove(false);
  }, [documentId]);

  // The collection name for the header eyebrow (cached; no extra round trip
  // when the collections grid was already visited).
  const { data: collections = [] } = useQuery({
    queryKey: qk.kb.collections,
    queryFn: () => client.listCollections(),
  });
  const collectionName =
    collections.find((c) => c.resource_id === doc?.collection_id)?.name ?? doc?.collection_id ?? "";

  const reindexMut = useMutation({
    mutationFn: () => client.reindexDocument(docId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.kb.doc(docId) });
      onChanged?.();
    },
  });
  const removeMut = useMutation({
    mutationFn: () => client.deleteDocument(docId),
    onSuccess: () => {
      onChanged?.();
      onClose();
    },
  });

  return (
    <>
      <div className="kb-drawer-backdrop" onClick={onClose} aria-hidden />
      <aside className="kb-docviewer" role="dialog" aria-label="Document">
        <header className="kb-docviewer__head">
          <div className="kb-docviewer__icon">
            <Icon name={kindIcon(doc?.filename ?? docId)} size={18} color="var(--text-paper-d)" />
          </div>
          <div className="kb-docviewer__titles">
            {collectionName && <div className="kb-docviewer__eyebrow">{collectionName}</div>}
            <h3 className="kb-docviewer__name">{doc?.filename ?? "Document"}</h3>
            {doc && (
              <div className="kb-docviewer__meta">
                <span>{fmtBytes(doc.size)}</span>
                <span aria-hidden>·</span>
                <span className={doc.cited > 0 ? "is-hot" : undefined}>cited {doc.cited}×</span>
                <span aria-hidden>·</span>
                <span>{doc.chunks} chunks</span>
                <span aria-hidden>·</span>
                <span>uploaded {fmtDate(doc.updated_at)}</span>
              </div>
            )}
          </div>
          <button type="button" className="kb-iconbtn" aria-label="Close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </header>

        <div className="kb-docviewer__actions">
          <a className="kb-btn kb-btn--sm" href={docHref(docId, snippet)} target="_blank" rel="noreferrer">
            <Icon name="external" size={13} /> Open full view
          </a>
          {doc && (
            <a className="kb-btn kb-btn--sm" href={blobHref(doc.file_id)} download={doc.filename}>
              <Icon name="download" size={13} /> Download
            </a>
          )}
          <span style={{ flex: 1 }} />
          <button
            type="button"
            className="kb-btn kb-btn--sm"
            disabled={reindexMut.isPending}
            onClick={() => reindexMut.mutate()}
          >
            <Icon name="refresh" size={13} /> Re-index
          </button>
          <button
            type="button"
            className="kb-btn kb-btn--sm"
            aria-label="Remove document"
            onClick={() => setConfirmRemove((v) => !v)}
          >
            <Icon name="x" size={13} /> Remove
          </button>
          {confirmRemove && (
            <div className="kb-colpage__confirm" role="dialog" aria-label="Confirm remove document">
              <span>Remove this document?</span>
              <button type="button" className="kb-btn kb-btn--danger kb-btn--sm" onClick={() => removeMut.mutate()}>
                Remove
              </button>
              <button type="button" className="kb-btn kb-btn--sm" onClick={() => setConfirmRemove(false)}>
                Cancel
              </button>
            </div>
          )}
        </div>

        <div className="kb-docviewer__body">
          <KbDocBody
            documentId={docId}
            snippet={snippet}
            onNavigate={setDocId}
            onLoaded={setDoc}
            client={client}
          />
        </div>
      </aside>
    </>
  );
}
