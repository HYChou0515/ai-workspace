/**
 * The documents a context card links (`reference_doc_ids`, #518) — shown so a
 * person can finally see and manage them. Before this the link existed only in
 * automation: a workflow/API set it and the retriever read it, but the card
 * editor omitted the field entirely, so to a human the doc↔card link was
 * invisible and uneditable.
 *
 * A card links docs, it does not own bytes (#513): these are opaque doc-id
 * tokens, never parsed for logic. We decode a *display* label only — the
 * document's filename — and fall back to the raw token if it doesn't decode.
 *
 * Removing a chip detaches the link only; the underlying document stays a
 * first-class KB citizen (the deliberate non-cascade design). Creating a link
 * by dropping a file is a later, heavier piece; this is see + detach.
 */

/** A doc-id is `encode_doc_id` = percent-encoded `collection/user/path`. For a
 * DISPLAY label we recover the path's basename; logic never parses it. */
export function docLabel(docId: string): string {
  try {
    const decoded = decodeURIComponent(docId);
    const path = decoded.split("/").slice(2).join("/") || decoded;
    const base = path.split("/").filter(Boolean).pop();
    return base || docId;
  } catch {
    return docId;
  }
}

export function CardAttachments({
  docIds,
  onDetach,
  onAttach,
  editable,
}: {
  docIds: string[];
  /** Detach one linked document (remove the reference_doc_ids entry). The file
   * itself is untouched — a sweeper never reaps it; it stays in the doc list. */
  onDetach?: (docId: string) => void;
  /** Drop or pick file(s) to link. The card uploads them (the normal ingest
   * pipeline) and links the resulting docs — "drop a picture on the card and
   * it's there." Absent ⇒ no attach affordance. */
  onAttach?: (files: FileList) => void;
  editable: boolean;
}) {
  const chips =
    docIds.length === 0 ? (
      editable ? (
        <p className="kb-cards__none" data-testid="card-attachments-empty">
          No linked documents.
        </p>
      ) : null
    ) : (
      <div className="kb-cards__attachments" data-testid="card-attachments">
        {docIds.map((id) => (
          <span key={id} className="kb-cards__chip" title={id}>
            {docLabel(id)}
            {editable && onDetach ? (
              <button
                type="button"
                aria-label={`Detach ${docLabel(id)}`}
                onClick={() => onDetach(id)}
              >
                ×
              </button>
            ) : null}
          </span>
        ))}
      </div>
    );

  if (!editable || !onAttach) return chips;

  return (
    <>
      {chips}
      <label
        className="kb-cards__attach-drop"
        data-testid="card-attach-drop"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (e.dataTransfer.files.length) onAttach(e.dataTransfer.files);
        }}
      >
        Drop an image or file here, or click to choose
        <input
          type="file"
          data-testid="card-attach-input"
          style={{ display: "none" }}
          onChange={(e) => {
            if (e.target.files?.length) onAttach(e.target.files);
            e.target.value = ""; // let the same file be picked again after a detach
          }}
        />
      </label>
    </>
  );
}
