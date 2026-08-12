/**
 * board view (#419 §B, #451 §A3) — records grouped into columns by a `status`
 * field. A card drags between columns (@dnd-kit) to change its status, with the
 * status select kept as an accessible / keyboard fallback; both ride the single
 * `update` write path. Empty vocab columns still render; a status outside the
 * closed vocabulary shows in its own degraded, non-droppable column (§D) so the
 * card never vanishes. Card faces show the picked fields as read-only role
 * widgets (actor avatar, progress bar, date). Registered as the `board` kind.
 */

import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { useEffect, useRef, useState } from "react";

import type { EntityFieldSpec, EntityInstance, EntityType } from "../../api/entities";
import type { User } from "../../api/types";
import { Popover } from "../../components/Popover";
import { handleDragEnd, partitionColumns, UNSET_COL } from "./boardOps";
import { selectColor } from "./selectColor";
import { sortRows } from "./sortRows";
import { RoleField, widgetForRole } from "./roleWidget";
import { fieldText, roleOf } from "./shared";
import { SelectChip } from "./TableView";
import type { EntityViewProps } from "./types";

export function BoardView({
  spec,
  type,
  entities,
  users,
  canWrite,
  refIndex,
  onPatch,
  onOpenRecord,
  onOpenRecordFile,
  busy,
}: EntityViewProps) {
  const readOnly = canWrite === false; // §E — a non-writer can't drag or change status
  const groupField = spec.group_by ?? "status";
  const statusSpec = roleOf(type, groupField);
  const { known, extra } = partitionColumns(statusSpec, entities, groupField);
  const titleField = spec.card?.title ?? "title";
  const badges = spec.card?.badges ?? [];
  // #GH-projects P4 — manual drag-reorder writes `rank`, but only when no field
  // sort is active (a sorted board follows the sort, GitHub's model). A card drop
  // still changes status either way.
  const sortActive = Boolean(spec.sort?.length);

  const sensors = useSensors(
    // A small drag threshold so a click on the card's status select / buttons
    // still registers as a click, not a drag.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor),
  );

  // Each column's cards follow the view's multi-level `spec.sort`, or — with none —
  // the manual `rank` order (drag position), same ordering the Table uses (#GH-projects).
  const cardsIn = (value: string | null) =>
    sortRows(
      entities.filter((e) => {
        const v = fieldText(e.fields[groupField]);
        return value === null ? v === "" : v === value;
      }),
      spec.sort,
      type,
      refIndex,
      users,
    );


  const renderCard = (e: EntityInstance) => (
    <Card
      key={e.number}
      entity={e}
      titleField={titleField}
      badges={badges}
      type={type}
      statusSpec={statusSpec}
      groupField={groupField}
      users={users}
      busy={busy}
      readOnly={readOnly}
      onPatch={onPatch}
      onOpenRecord={onOpenRecord}
      onOpenRecordFile={onOpenRecordFile}
    />
  );

  const unset = cardsIn(null);

  return (
    <DndContext sensors={sensors} onDragEnd={(e) => handleDragEnd(e, groupField, onPatch, entities, sortActive)}>
      <div className="ev-board scrollable">
        {known.map((value) => (
          <DroppableColumn key={value} value={value} label={value} count={cardsIn(value).length} statusSpec={statusSpec}>
            {cardsIn(value).map(renderCard)}
          </DroppableColumn>
        ))}
        {/* out-of-vocab values (a lint warning): visible but NOT drop targets —
            you can't set an invalid status by dragging into it (§D). */}
        {extra.map((value) => (
          <DegradedColumn key={value} value={value} count={cardsIn(value).length}>
            {cardsIn(value).map(renderCard)}
          </DegradedColumn>
        ))}
        {unset.length > 0 && (
          <DroppableColumn value={UNSET_COL} label="(unset)" count={unset.length}>
            {unset.map(renderCard)}
          </DroppableColumn>
        )}
      </div>
    </DndContext>
  );
}

// ── columns ──────────────────────────────────────────────────────────────────

function DroppableColumn({
  value,
  label,
  count,
  statusSpec,
  children,
}: {
  value: string;
  label: string;
  count: number;
  statusSpec?: EntityFieldSpec;
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `col-${value}` });
  // A colour dot on the header — the GitHub-Projects column colour (#GH-projects
  // B). The unset column stays neutral.
  const dot = value === UNSET_COL ? undefined : selectColor(value, statusSpec).fg;
  return (
    <div ref={setNodeRef} className={`ev-board__col${isOver ? " ev-board__col--over" : ""}`}>
      <div className="ev-board__col-head" data-testid={`col-${value === UNSET_COL ? "unset" : value}`}>
        <span className="ev-board__col-name">
          {dot && <span className="ev-board__col-dot" style={{ background: dot }} aria-hidden />}
          {label}
        </span>
        <span className="ev-board__count">{count}</span>
      </div>
      {children}
    </div>
  );
}

function DegradedColumn({ value, count, children }: { value: string; count: number; children: React.ReactNode }) {
  return (
    <div className="ev-board__col ev-board__col--degraded">
      <div
        className="ev-board__col-head"
        data-testid={`col-${value}`}
        title="status is outside the field's allowed values"
      >
        <span className="ev-board__col-name">⚠ {value}</span>
        <span className="ev-board__count">{count}</span>
      </div>
      {children}
    </div>
  );
}

// ── card ─────────────────────────────────────────────────────────────────────

function Card({
  entity,
  titleField,
  badges,
  type,
  statusSpec,
  groupField,
  users,
  busy,
  readOnly,
  onPatch,
  onOpenRecord,
  onOpenRecordFile,
}: {
  entity: EntityInstance;
  titleField: string;
  badges: string[];
  type: EntityType | null;
  statusSpec: EntityFieldSpec | undefined;
  groupField: string;
  users?: User[];
  busy?: boolean;
  readOnly?: boolean;
  onPatch: (number: number, patch: Record<string, unknown>) => void;
  onOpenRecord?: (number: number) => void;
  onOpenRecordFile?: (number: number) => void;
}) {
  // §E — a read-only member can neither drag the card nor change its status.
  const { attributes, listeners, setNodeRef, transform } = useDraggable({
    id: `card-${entity.number}`,
    disabled: readOnly,
  });
  // #GH-projects P4 — a card is also a DROP target, so dropping another card onto
  // it reorders in front of it (manual `rank`). Same id as the draggable; dnd-kit
  // keeps the draggable + droppable registries separate.
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: `card-${entity.number}` });
  const setRefs = (node: HTMLElement | null) => {
    setNodeRef(node);
    setDropRef(node);
  };

  // The card face only shows read-only badges + the status select, so the ⋯ menu
  // is the way to reach the rest of the fields (Open) or the raw file (Open file).
  // Open is NOT gated on write permission: it lands on the reading view, and a
  // read-only member had no way at all to read a card's body before.
  const canOpen = !!onOpenRecord && !!type;
  const canOpenFile = !!onOpenRecordFile;

  return (
    <div
      ref={setRefs}
      data-testid={`card-${entity.number}`}
      data-over={isOver ? "" : undefined}
      className={`ev-card${readOnly ? " ev-card--readonly" : ""}`}
      style={{ transform: transform ? `translate(${transform.x}px, ${transform.y}px)` : undefined }}
      // #680 — same gesture as the gantt bar and the table's #N cell. Controls
      // inside the card stop it, so double-clicking the status chip edits the
      // status instead of opening the record.
      onDoubleClick={() => onOpenRecord?.(entity.number)}
      {...attributes}
      {...listeners}
    >
      <div className="ev-card__head">
        {/* The number is the record's spoken name ("look at 33"), so it rides
            every card — it used to be a FALLBACK for a missing title, which
            meant every card that had a title hid the one thing people say out
            loud. Muted and ahead of the title; not a control, because the card
            itself is the drag handle and the double-click target. */}
        <div className="ev-card__title">
          <span className="ev-card__num">#{entity.number}</span>
          {fieldText(entity.fields[titleField]) && (
            <span className="ev-card__label">{fieldText(entity.fields[titleField])}</span>
          )}
        </div>
        {(canOpen || canOpenFile) && (
          // Isolate the menu from the card's drag listeners — a pointerdown here
          // must not arm a drag — and from its open gesture, so double-clicking
          // the menu doesn't also throw the record over the board.
          <div
            className="ev-card__menu"
            onPointerDown={(e) => e.stopPropagation()}
            onDoubleClick={(e) => e.stopPropagation()}
          >
            <Popover
              align="end"
              width={160}
              trigger={({ onClick, open }) => (
                <button
                  type="button"
                  className="ev-card__menu-btn"
                  aria-label={`card ${entity.number} menu`}
                  aria-expanded={open}
                  onClick={onClick}
                >
                  ⋯
                </button>
              )}
            >
              {(close) => (
                <div className="ev-cardmenu">
                  {canOpen && (
                    <button
                      type="button"
                      className="ev-cardmenu__item"
                      onClick={() => {
                        onOpenRecord?.(entity.number);
                        close();
                      }}
                    >
                      Open
                    </button>
                  )}
                  {canOpenFile && (
                    <button
                      type="button"
                      className="ev-cardmenu__item"
                      onClick={() => {
                        onOpenRecordFile?.(entity.number);
                        close();
                      }}
                    >
                      Open file
                    </button>
                  )}
                </div>
              )}
            </Popover>
          </div>
        )}
      </div>
      {badges.length > 0 && (
        <div className="ev-card__badges">
          {badges.map((b) => (
            <CardBadge key={b} name={b} value={entity.fields[b]} spec={roleOf(type, b)} users={users} />
          ))}
        </div>
      )}
      {statusSpec && statusSpec.values && (
        // Stop pointerdown here from arming the card's drag sensor, so a click on
        // the chip / select is a click, not the start of a drag — and stop the
        // double-click, so editing the status doesn't also open the record.
        <div
          className="ev-card__status-row"
          onPointerDown={(e) => e.stopPropagation()}
          onDoubleClick={(e) => e.stopPropagation()}
        >
          <CardStatus
            spec={statusSpec}
            value={entity.fields[groupField]}
            disabled={busy || readOnly}
            onCommit={(next) => onPatch(entity.number, { [groupField]: next })}
          />
        </div>
      )}
    </div>
  );
}

/** The card's status as a coloured chip at rest (GitHub-Projects style, #GH-
 * projects B / #3); a click swaps to the native select — the keyboard-accessible
 * way to move a card without dragging. Read-only → the chip alone, no control.
 * Mirrors the table cell's click-to-edit so both views feel the same. */
function CardStatus({
  spec,
  value,
  disabled,
  onCommit,
}: {
  spec: EntityFieldSpec;
  value: unknown;
  disabled?: boolean;
  onCommit: (next: unknown) => void;
}) {
  const [editing, setEditing] = useState(false);
  const boxRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (editing) boxRef.current?.querySelector<HTMLSelectElement>("select")?.focus();
  }, [editing]);

  const display = fieldText(value);
  const chip = display ? (
    <SelectChip value={display} fieldSpec={spec} />
  ) : (
    <span className="ev-cell__empty">—</span>
  );

  if (disabled) return <div className="ev-card__status">{chip}</div>;

  if (!editing) {
    return (
      <button
        type="button"
        className="ev-cell ev-card__status"
        aria-label={`edit ${spec.name}`}
        onClick={() => setEditing(true)}
      >
        {chip}
      </button>
    );
  }

  return (
    <span
      ref={boxRef}
      className="ev-card__status"
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setEditing(false);
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") setEditing(false);
      }}
    >
      <RoleField
        widget={widgetForRole(spec.role)}
        name={spec.name}
        value={value}
        values={spec.values}
        onCommit={(next) => {
          onCommit(next);
          setEditing(false);
        }}
      />
    </span>
  );
}

// ── card-face badges (read-only role widgets) ────────────────────────────────

function CardBadge({
  name,
  value,
  spec,
  users,
}: {
  name: string;
  value: unknown;
  spec: EntityFieldSpec | undefined;
  users?: User[];
}) {
  const role = spec?.role;

  if (role === "progress") {
    if (value == null || value === "") return null;
    const pct = Math.max(0, Math.min(100, Number(value) || 0));
    return (
      <span aria-label={`${name} ${pct}%`} title={`${name} ${pct}%`} className="ev-progress">
        <span className="ev-progress__bar" style={{ width: `${pct}%` }} />
      </span>
    );
  }

  const text = fieldText(value);
  if (!text) return null;

  if (role === "actor") {
    const u = users?.find((x) => x.id === text);
    return (
      <span className="ev-card__badge" title={name}>
        <MiniAvatar name={u?.name ?? text} photo={u?.photo_url ?? undefined} />
        {u?.name ?? text}
      </span>
    );
  }

  return (
    <span className="ev-card__badge" title={name}>
      {text}
    </span>
  );
}

function MiniAvatar({ name, photo }: { name: string; photo?: string }) {
  const initials =
    (name || "?")
      .split(/[\s_-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((s) => s[0]?.toUpperCase() ?? "")
      .join("") || "?";
  return (
    <span
      aria-hidden
      className="ev-avatar"
      style={photo ? { backgroundImage: `url(${photo})` } : undefined}
    >
      {photo ? "" : initials}
    </span>
  );
}
