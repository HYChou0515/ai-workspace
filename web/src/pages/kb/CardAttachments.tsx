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

import type React from "react";

import { Icon } from "../../components/Icon";

/** U+2215 DIVISION SLASH — what `encode_doc_id` substitutes for every ASCII "/"
 * so the id stays slash-free and a path round-trips a URL untouched. */
const DOC_ID_SLASH = "\u2215";

/** A doc-id is `encode_doc_id(collection_id, path)`: the two joined by "/", with
 * EVERY ASCII slash — the separator and any inside the path — rewritten to
 * U+2215. For a DISPLAY label we recover the path's basename; logic never
 * parses it.
 *
 * This used to split on ASCII "/" after a `decodeURIComponent`, describing a
 * three-part percent-encoded `collection/user/path` that the backend does not
 * produce and, being path-keyed rather than per-user, never did. Nothing
 * matched, so the fallback fired and every non-image attachment displayed its
 * raw id — `collection:288b59a1-…∕M4∕report.csv` where a filename belonged. The
 * unit tests agreed with the code because they BUILT their fixtures the same
 * wrong way; only a real document proved otherwise. */
export function docLabel(docId: string): string {
  const parts = docId.split(DOC_ID_SLASH).filter(Boolean);
  // [collection, ...path segments] — anything shorter has no path to show.
  const base = parts.length > 1 ? parts[parts.length - 1] : "";
  return base || docId;
}

export function CardAttachments({
  docIds,
  onDetach,
  onAttach,
  onOpen,
  imageSrc,
  editable,
  tileSize,
}: {
  docIds: string[];
  /** Detach one linked document (remove the reference_doc_ids entry). The file
   * itself is untouched — a sweeper never reaps it; it stays in the doc list. */
  onDetach?: (docId: string) => void;
  /** Drop or pick file(s) to link. The card uploads them (the normal ingest
   * pipeline) and links the resulting docs — "drop a picture on the card and
   * it's there." Absent ⇒ no attach affordance. */
  onAttach?: (files: FileList) => void;
  /** Open a linked document in the shared viewer drawer. A link you can't open
   * is dead weight, so the filename becomes a button whenever an opener is
   * wired — in preview as well as edit, since opening never mutates the card.
   * Absent ⇒ the name is plain text. */
  onOpen?: (docId: string) => void;
  /** A displayable image URL for a linked doc, or undefined for non-images. An
   * image attachment renders as an actual thumbnail (still click-to-open,
   * still detachable) instead of a filename pill — a picture is worth more than
   * its name. Absent resolver ⇒ everything is a pill. */
  imageSrc?: (docId: string) => string | undefined;
  editable: boolean;
  /** Tile size in px — the grid's `minmax()` floor, so `auto-fill` still picks
   * the column count. Omitted ⇒ the stored default. */
  tileSize?: number;
}) {
  const chips =
    docIds.length === 0 ? (
      editable ? (
        <p className="kb-cards__none" data-testid="card-attachments-empty">
          No linked documents.
        </p>
      ) : null
    ) : (
      <div
        className="kb-cards__attachments"
        data-testid="card-attachments"
        style={
          tileSize
            ? ({ "--kb-tile": `${tileSize}px` } as React.CSSProperties)
            : undefined
        }
      >
        {docIds.map((id) => {
          const label = docLabel(id);
          const src = imageSrc?.(id);
          const detach =
            editable && onDetach ? (
              <button
                type="button"
                className="kb-cards__tile-detach"
                aria-label={`Detach ${label}`}
                onClick={() => onDetach(id)}
              >
                ×
              </button>
            ) : null;

          // ONE tile shape, whatever the file is. The two used to be different
          // objects — a cropped 72px square for images, a text pill for
          // everything else — so a card mixing photos and a spec sheet read as
          // two unrelated lists. What differs is only what the tile SHOWS: a
          // picture that can be previewed shows itself; anything else shows an
          // icon over its name.
          const face = src ? (
            // `contain`, not `cover`: these are annotated photographs and the
            // mark is as often at an edge as in the middle. A cropped thumbnail
            // of a defect photo is indistinguishable from the next one, which
            // forces a person to open each in turn.
            <img className="kb-cards__tile-img" src={src} alt={label} />
          ) : (
            <span className="kb-cards__tile-file">
              <Icon name="file" size={20} />
              <span className="kb-cards__tile-name">{label}</span>
            </span>
          );

          return (
            <span key={id} className="kb-cards__tile" title={id}>
              {onOpen ? (
                <button
                  type="button"
                  className="kb-cards__tile-open"
                  aria-label={`Open ${label}`}
                  onClick={() => onOpen(id)}
                >
                  {face}
                </button>
              ) : (
                face
              )}
              {detach}
            </span>
          );
        })}
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
