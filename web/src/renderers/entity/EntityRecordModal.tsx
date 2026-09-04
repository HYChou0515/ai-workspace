/**
 * EntityRecordModal (#680) — one record, opened in place from an entity view.
 *
 * Double-clicking a gantt bar / table row / board card opens this instead of
 * navigating away, because the point of the gesture is to look at a record
 * WITHOUT losing the chart you were reading it from. It renders the same
 * `EntityRecordPane` as the record's file tab (read first, Edit flips to the
 * form) so the two routes can't drift apart.
 *
 * It deliberately does NOT replace the file tab. The tab strip's raw whole-file
 * Edit toggle — the escape hatch for repairing a file the structured surfaces
 * can't express — only exists there, so the modal offers a way OVER to it. When
 * there's no workspace to open into (`useOpenFile()` is null, e.g. a standalone
 * preview or a test) the control isn't rendered at all rather than rendered
 * dead. Reaching this modal means the view — and so the file pane — is on
 * screen, which is why visibility isn't checked as well.
 *
 * Presentational: the caller owns the record, the write path (`useEntityWrite`,
 * §B1) and the conflict list, exactly as the file tab's container does.
 */

import { useState } from "react";

import type { EntityInstance, EntityType } from "../../api/entities";
import type { User } from "../../api/types";
import { ModalShell } from "../../components/ModalShell";
import { useDirtyClose } from "../../hooks/useDirtyClose";
import { useOpenFile } from "../../hooks/openFile";
import { EntityRecordPane } from "./EntityRecordPane";
import type { RefOption } from "./refTraversal";
import { ConflictBanner } from "./shared";

export type EntityRecordModalProps = {
  type: EntityType;
  record: EntityInstance;
  users?: User[];
  canWrite?: boolean;
  busy?: boolean;
  /** Record numbers whose optimistic write hit a 409 (§B2). The banner shows
   * while this record is among them. */
  conflicts?: number[];
  onDismissConflict?: (number: number) => void;
  refOptionsFor?: (name: string) => RefOption[] | undefined;
  onSave: (patch: Record<string, unknown>, body: string) => void;
  onClose: () => void;
};

/** `{records_path}/{number}.md` — the file this record IS. Normalised so a
 * `records_path` written with a leading slash still yields a workspace path. */
export function recordPath(type: EntityType, record: EntityInstance): string {
  return `${type.records_path.replace(/^\/+/, "").replace(/\/+$/, "")}/${record.number}.md`;
}

export function EntityRecordModal({
  type,
  record,
  users,
  canWrite,
  busy,
  conflicts,
  onDismissConflict,
  refOptionsFor,
  onSave,
  onClose,
}: EntityRecordModalProps) {
  const openFile = useOpenFile();
  // An unsaved edit lives in the pane, so every exit from this modal drops it.
  //
  // The two ACCIDENTAL exits are withdrawn outright while the form is open: the
  // file route (which would swap the surface out from under the typing) and the
  // backdrop (a stray click beside the panel). Prompting about those would be
  // its own interruption — the user never meant to trigger them.
  //
  // Escape and ✕ stay, because a modal you can't dismiss is worse than a lost
  // draft. #779 keeps that and adds the missing half: they ask once instead of
  // dropping the edit silently, and the prompt's own "discard" is the way out,
  // so the modal is still dismissable. This pane is a text editor, and Escape
  // is what people press to dismiss ITS popups — which is exactly how a
  // deliberate keystroke ends up costing an edit nobody meant to abandon.
  const [editing, setEditing] = useState(false);
  const path = recordPath(type, record);
  const title = String(record.fields.title ?? type.name);
  const inConflict = (conflicts ?? []).includes(record.number);
  const attemptClose = useDirtyClose(editing, onClose);

  return (
    <ModalShell
      onClose={attemptClose}
      // Several records get opened in one session, so the accessible name has to
      // say which one — "Record" alone tells a screen-reader user nothing.
      ariaLabel={`#${record.number} ${title}`}
      data-testid="entity-record-modal"
      closeOnBackdrop={!editing}
      width={720}
      maxWidth="94vw"
      panelClassName="ev-record-modal"
    >
      <div className="ev-record-modal__bar">
        {openFile && !editing && (
          <button
            type="button"
            className="btn"
            data-variant="ghost"
            data-size="sm"
            // dirty-close-exempt: a handover, not an exit — the record opens in
            // a file tab, so the work continues there rather than being dropped.
            // The button is hidden while editing (`!editing` above), so there is
            // never an unsaved form behind this click.
            onClick={() => {
              openFile(path);
              // Hand over rather than stack: the file tab now shows this record,
              // and leaving the modal up would cover the thing just opened.
              onClose();
            }}
          >
            Open file
          </button>
        )}
        <button
          type="button"
          className="btn"
          data-variant="ghost"
          data-size="sm"
          aria-label="close record"
          onClick={attemptClose}
        >
          ✕
        </button>
      </div>

      {inConflict && <ConflictBanner conflicts={[record.number]} onDismiss={onDismissConflict} />}

      <EntityRecordPane
        path={path}
        type={type}
        record={record}
        users={users}
        canWrite={canWrite}
        busy={busy}
        refOptionsFor={refOptionsFor}
        onSave={onSave}
        onEditingChange={setEditing}
      />
    </ModalShell>
  );
}
