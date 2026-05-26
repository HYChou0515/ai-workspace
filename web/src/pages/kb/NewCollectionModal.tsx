/**
 * "New collection" modal — name (required) + optional description. Replaces the
 * inline create row on the grid landing; the parent owns the create mutation
 * and passes `onCreate`. Closes on Escape / backdrop / Cancel.
 */

import { useEffect, useState } from "react";

import { Icon } from "../../components/Icon";

export function NewCollectionModal({
  open,
  onClose,
  onCreate,
  busy = false,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (name: string, description: string) => void;
  busy?: boolean;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  // Reset fields each time the modal opens.
  useEffect(() => {
    if (open) {
      setName("");
      setDescription("");
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const canCreate = name.trim().length > 0 && !busy;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canCreate) return;
    onCreate(name.trim(), description.trim());
  };

  return (
    <div className="kb-modal" role="presentation" onClick={onClose}>
      <form
        className="kb-modal__card"
        role="dialog"
        aria-modal
        aria-label="New collection"
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <header className="kb-modal__head">
          <div className="caps">Knowledge base</div>
          <h2 className="kb-modal__title">New collection</h2>
        </header>

        <div className="kb-modal__body">
          <label className="kb-field">
            <span className="kb-field__label">
              Name<span className="kb-field__req"> *</span>
            </span>
            <input
              className="kb-input"
              // biome-ignore lint/a11y/noAutofocus: the name field is the modal's primary input
              autoFocus
              placeholder="New collection name…"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="kb-field">
            <span className="kb-field__label">Description</span>
            <textarea
              className="kb-input kb-textarea"
              placeholder="What lives in this collection?"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
        </div>

        <footer className="kb-modal__foot">
          <button type="button" className="kb-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="kb-btn kb-btn--primary" disabled={!canCreate}>
            <Icon name="plus" size={13} /> Create
          </button>
        </footer>
      </form>
    </div>
  );
}
