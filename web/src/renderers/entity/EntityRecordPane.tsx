/**
 * EntityRecordPane — the two states of one record: READ it, or EDIT it.
 *
 * Lifted out of `RecordFileRenderer` (#680) because a record is now reachable
 * two ways: opening `issues/7.md` in the workspace IDE, and double-clicking a
 * bar / row / card in an entity view, which opens the same thing in a modal.
 * Both routes must show the SAME surface — a modal that renders its own lighter
 * form would drift from the file tab within a release — so the pane lives here
 * and each container supplies only the record + the write handler.
 *
 * Pure/presentational: no queries, no write hook. The caller resolves the record
 * (both routes already hold the projected list — `EntityInstance` carries the
 * body, so neither needs an extra fetch) and hands in `onSave`, which is always
 * the shared `useEntityWrite.save` (§B1 single write path, so the optimistic +
 * 409 contract is inherited rather than re-implemented).
 *
 * Note there is a THIRD state neither container owns: the tab strip's raw
 * whole-file Edit toggle, the escape hatch for repairing a file the structured
 * surfaces can't express. It only exists in the file tab, which is why the modal
 * offers a way over to it instead of pretending to replace it.
 */

import { useState } from "react";

import type { EntityInstance, EntityType } from "../../api/entities";
import type { User } from "../../api/types";
import { EntityFileEditor } from "./EntityFileEditor";
import { EntityRecordView } from "./EntityRecordView";
import type { RefOption } from "./refTraversal";

export type EntityRecordPaneProps = {
  /** The record file's own path — the base the reading view resolves relative
   * links and images against, exactly as any `.md` file does. */
  path: string;
  type: EntityType;
  record: EntityInstance;
  users?: User[];
  canWrite?: boolean;
  busy?: boolean;
  refOptionsFor?: (name: string) => RefOption[] | undefined;
  onSave: (patch: Record<string, unknown>, body: string) => void;
  /** Told whenever the pane enters / leaves the form. A container that can be
   * dismissed (the modal) uses it to withdraw its own exits while there are
   * unsaved edits — the form's state lives here, so a dismissal drops it. */
  onEditingChange?: (editing: boolean) => void;
};

export function EntityRecordPane({
  path,
  type,
  record,
  users,
  canWrite,
  busy,
  refOptionsFor,
  onSave,
  onEditingChange,
}: EntityRecordPaneProps) {
  const [editing, setEditing] = useState(false);
  const setMode = (next: boolean) => {
    setEditing(next);
    onEditingChange?.(next);
  };
  if (!editing) {
    return (
      <EntityRecordView
        type={type}
        record={record}
        users={users}
        path={path}
        canWrite={canWrite}
        refOptionsFor={refOptionsFor}
        onEdit={() => setMode(true)}
      />
    );
  }
  return (
    <EntityFileEditor
      type={type}
      record={record}
      users={users}
      canWrite={canWrite}
      busy={busy}
      refOptionsFor={refOptionsFor}
      onSave={(patch, body) => {
        onSave(patch, body);
        // Back to reading — the write is optimistic, so the view shows the new
        // values immediately, and a 409 arrives as the container's banner.
        setMode(false);
      }}
      onCancel={() => setMode(false)}
    />
  );
}
