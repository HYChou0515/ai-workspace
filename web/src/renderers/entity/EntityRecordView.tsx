/**
 * EntityRecordView — the READING state of a record file (`issues/7.md`).
 *
 * Opening a record used to drop you straight into the edit form: eight input
 * boxes and the body as raw markdown source in Monaco. That is the right
 * surface for changing a record and the wrong one for reading it — a body with
 * headings, lists and tables is exactly the content that reads worst as source.
 *
 * So a record now opens here, and `Edit` flips to the form. The third state,
 * the tab strip's raw whole-file toggle, is untouched: it stays the escape
 * hatch for repairing a file the structured surfaces can't express.
 *
 * Values are shown through the SAME `cellDisplay` the table uses, so a
 * milestone reads as its title and an assignee as their name here too; the
 * body goes through the SAME markdown pipeline as any `.md` file, so an image
 * or a relative link behaves the way it does everywhere else.
 */

import type { EntityInstance, EntityType } from "../../api/entities";
import type { User } from "../../api/types";
import { pxToRem } from "../../lib/pxToRem";
import { MarkdownBody } from "../MarkdownRenderer";
import type { RefOption } from "./refTraversal";
import { cellDisplay, SelectChip } from "./TableView";

export type EntityRecordViewProps = {
  type: EntityType;
  record: EntityInstance;
  users?: User[];
  /** The record file's own path — the base for resolving relative links and
   * images in the body, exactly as the markdown renderer resolves them. */
  path: string;
  canWrite?: boolean;
  refOptionsFor?: (name: string) => RefOption[] | undefined;
  onEdit: () => void;
};

export function EntityRecordView({
  type,
  record,
  users,
  path,
  canWrite = true,
  refOptionsFor,
  onEdit,
}: EntityRecordViewProps) {
  // `rank` is the manual drag order — infrastructure the user never types. The
  // table already excludes it; showing it here (as the form still did) is noise
  // at best, and an invitation to break your own ordering at worst.
  const shown = type.fields.filter((f) => f.role !== "rank" && f.name !== "title");
  const body = record.body ?? "";

  return (
    <div className="ev-editor">
      <div className="ev-editor__head">
        <h3 className="ev-editor__title">
          <span className="ev-editor__title-num">#{record.number}</span>
          {String(record.fields.title ?? type.name)}
        </h3>
        {canWrite && (
          <button type="button" className="btn" data-variant="secondary" data-size="sm" onClick={onEdit}>
            Edit
          </button>
        )}
      </div>

      <dl className="ev-record__fields" data-testid="record-fields">
        {shown.map((f) => {
          const value = record.fields[f.name];
          const text = cellDisplay(f, value, users, refOptionsFor?.(f.name));
          return (
            <div key={f.name} className="ev-record__field">
              <dt className="ev-editor__label">{f.name}</dt>
              <dd className="ev-record__value">
                {text === "" ? (
                  <span className="ev-cell__empty">—</span>
                ) : f.role === "status" ? (
                  <SelectChip value={text} fieldSpec={f} />
                ) : (
                  text
                )}
              </dd>
            </div>
          );
        })}
      </dl>

      {body.trim() === "" ? (
        <p style={{ color: "var(--text-paper-d)", fontSize: pxToRem(13) }}>
          Nothing written here yet.
        </p>
      ) : (
        <MarkdownBody text={body} path={path} />
      )}
    </div>
  );
}
