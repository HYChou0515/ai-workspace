/**
 * #715: what an archive import is doing, while it does it.
 *
 * The synchronous import had nothing to show — the request either came back or
 * timed out. The asynchronous one answers before the work starts, so silence
 * here would be worse than the old flow, not better: the user would pick a file
 * and watch nothing happen. This is the whole reason the run carries `members`
 * and `written`.
 *
 * `finished` is reported separately from SUCCESS on purpose. A half-applied
 * import finishes with `written < members` and a reason per document, and
 * telling someone "done" over that is the failure the feature exists to remove.
 */

import type { ArchiveImport } from "../../api/kb";
import { Icon } from "../../components/Icon";

export function ArchiveImportStatus({
  run,
  onDismiss,
}: {
  run: ArchiveImport | null;
  onDismiss: () => void;
}) {
  if (!run) return null;

  const complete = run.finished && run.written >= run.members && run.errors.length === 0;
  const pct = run.members > 0 ? Math.round((run.written / run.members) * 100) : 0;

  return (
    <div
      className="kb-import-status"
      role="status"
      aria-live="polite"
      data-state={run.finished ? (complete ? "done" : "partial") : "running"}
    >
      <Icon name={run.finished ? (complete ? "check" : "flame") : "upload"} size={13} />
      <span className="kb-import-status__text">
        {run.finished
          ? complete
            ? `Imported ${run.written} of ${run.members} documents`
            : `Finished with ${run.members - run.written} of ${run.members} documents not imported`
          : `Importing ${run.written} of ${run.members} documents…`}
      </span>
      {!run.finished && (
        <span className="kb-import-status__bar" aria-hidden="true">
          <span className="kb-import-status__fill" style={{ width: `${pct}%` }} />
        </span>
      )}
      {run.errors.length > 0 && (
        <details className="kb-import-status__errors">
          {/* The list is capped server-side at 100 lines, so say so rather than
              letting a reader count them and conclude that is all that failed. */}
          <summary>{`Why (${run.errors.length}${run.errors.length >= 100 ? "+" : ""})`}</summary>
          <ul>
            {run.errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </details>
      )}
      {run.finished && (
        <button type="button" className="kb-btn" onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </div>
  );
}
