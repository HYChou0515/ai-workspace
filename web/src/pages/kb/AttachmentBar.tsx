/**
 * #513 P8 — the attachment card list shown BELOW an open document. An
 * attachment is an ordinary child SourceDoc under the parent's reserved `.att/`
 * namespace (a fetched figure or a manually-added file), so the list is type-
 * agnostic: each card faithfully shows name · type · size and opens the SAME
 * `KbDocViewer` drawer as any document. The row header adds one, each card can
 * replace / rename / delete it — all through the ordinary document endpoints
 * (upload to a path, upload to the same path, move, delete). No per-type code.
 */

import { useRef, useState } from "react";

import type { KbDocument } from "../../api/kb";
import { Icon } from "../../components/Icon";
import { kindIcon } from "./docKind";

function basename(path: string): string {
  return path.split("/").pop() || path;
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${Math.round(n / (1024 * 1024))} MB`;
}

export function AttachmentBar({
  parentPath,
  attachments,
  onOpen,
  onUpload,
  onReplace,
  onDelete,
  onRename,
}: {
  /** The open document's path — attachments hang under `{parentPath}/.att/`. */
  parentPath: string;
  attachments: KbDocument[];
  /** Open an attachment in the shared KbDocViewer drawer. */
  onOpen: (documentId: string) => void;
  onUpload: (file: File) => void;
  onReplace: (att: KbDocument, file: File) => void;
  onDelete: (att: KbDocument) => void;
  /** Rename the tail after `.att/` (the parent + prefix are fixed). */
  onRename: (att: KbDocument, newName: string) => void;
}) {
  const uploadRef = useRef<HTMLInputElement>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  // parentPath is documented as the anchor for `{parent}/.att/` targets; the
  // handlers build those paths, so it's a prop the wiring reads, not this view.
  void parentPath;

  return (
    <section className="kb-att" data-testid="kb-attachments" aria-label="Attachments">
      <header className="kb-att__head">
        <span className="kb-att__title">
          <Icon name="paperclip" size={13} /> Attachments ({attachments.length})
        </span>
        <button
          type="button"
          className="kb-btn kb-btn--sm"
          onClick={() => uploadRef.current?.click()}
        >
          <Icon name="paperclip" size={13} /> Add
        </button>
        <input
          ref={uploadRef}
          type="file"
          hidden
          data-testid="kb-att-upload-input"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onUpload(f);
            e.target.value = "";
          }}
        />
      </header>
      {attachments.length > 0 && (
        <ul className="kb-att__list">
          {attachments.map((a) => {
            const name = basename(a.path);
            return (
              <li key={a.resource_id} className="kb-att__card">
                {renaming === a.resource_id ? (
                  <input
                    className="kb-att__rename"
                    autoFocus
                    value={draft}
                    aria-label={`rename ${name} to`}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        const next = draft.trim();
                        if (next && next !== name) onRename(a, next);
                        setRenaming(null);
                      } else if (e.key === "Escape") {
                        setRenaming(null);
                      }
                    }}
                    onBlur={() => setRenaming(null)}
                  />
                ) : (
                  <button
                    type="button"
                    className="kb-att__open"
                    aria-label={`open ${name}`}
                    onClick={() => onOpen(a.resource_id)}
                  >
                    <Icon name={kindIcon(a.path)} size={15} />
                    <span className="kb-att__name">{name}</span>
                    <span className="kb-att__meta">
                      {a.content_type} · {fmtBytes(a.size ?? 0)}
                    </span>
                  </button>
                )}
                <div className="kb-att__actions">
                  <button
                    type="button"
                    className="kb-iconbtn"
                    aria-label={`rename ${name}`}
                    onClick={() => {
                      setDraft(name);
                      setRenaming(a.resource_id);
                    }}
                  >
                    <Icon name="pencil" size={13} />
                  </button>
                  <button
                    type="button"
                    className="kb-iconbtn"
                    aria-label={`replace ${name}`}
                    onClick={(e) => {
                      const input = e.currentTarget.nextElementSibling as HTMLInputElement | null;
                      input?.click();
                    }}
                  >
                    <Icon name="refresh" size={13} />
                  </button>
                  <input
                    type="file"
                    hidden
                    data-testid={`kb-att-replace-${a.resource_id}`}
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) onReplace(a, f);
                      e.target.value = "";
                    }}
                  />
                  <button
                    type="button"
                    className="kb-iconbtn"
                    aria-label={`delete ${name}`}
                    onClick={() => onDelete(a)}
                  >
                    <Icon name="trash" size={13} />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
