/**
 * The confirm step of "merge an archive into this collection" — the one place a
 * person is asked what should happen to what is already here.
 *
 * It governs context cards as well as documents (#701): a document collides on its
 * path, a card on its term, and the same click decides both. Naming only documents
 * asked about one thing and decided another. Extracted from the page so that
 * sentence has a test of its own — the page is 1100 lines of queries, so the copy
 * shipped unguarded and could be reverted with the whole suite still green.
 */

export function ImportModeDialog({
  fileName,
  busy,
  onOverwrite,
  onSkip,
  onCancel,
}: {
  fileName: string;
  /** The import is in flight — the two committing choices are disabled, Cancel is not. */
  busy: boolean;
  onOverwrite: () => void;
  onSkip: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="kb-colpage__confirm" role="dialog" aria-label="Import into collection">
      <span>Import “{fileName}” — for documents and cards that already exist?</span>
      <button type="button" className="kb-btn kb-btn--danger" disabled={busy} onClick={onOverwrite}>
        Overwrite
      </button>
      <button type="button" className="kb-btn" disabled={busy} onClick={onSkip}>
        Skip existing
      </button>
      <button type="button" className="kb-btn" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}
