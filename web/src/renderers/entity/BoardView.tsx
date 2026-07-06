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

import type { EntityFieldSpec, EntityInstance, EntityType } from "../../api/entities";
import type { User } from "../../api/types";
import { handleDragEnd, partitionColumns, UNSET_COL } from "./boardOps";
import { RoleField, widgetForRole } from "./roleWidget";
import { fieldText, roleOf } from "./shared";
import type { EntityViewProps } from "./types";

export function BoardView({ spec, type, entities, users, canWrite, onPatch, busy }: EntityViewProps) {
  const readOnly = canWrite === false; // §E — a non-writer can't drag or change status
  const groupField = spec.group_by ?? "status";
  const statusSpec = roleOf(type, groupField);
  const { known, extra } = partitionColumns(statusSpec, entities, groupField);
  const titleField = spec.card?.title ?? "title";
  const badges = spec.card?.badges ?? [];

  const sensors = useSensors(
    // A small drag threshold so a click on the card's status select / buttons
    // still registers as a click, not a drag.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor),
  );

  const cardsIn = (value: string | null) =>
    entities.filter((e) => {
      const v = fieldText(e.fields[groupField]);
      return value === null ? v === "" : v === value;
    });

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
    />
  );

  const unset = cardsIn(null);

  return (
    <DndContext sensors={sensors} onDragEnd={(e) => handleDragEnd(e, groupField, onPatch)}>
      <div className="ev-board scrollable">
        {known.map((value) => (
          <DroppableColumn key={value} value={value} label={value} count={cardsIn(value).length}>
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
  children,
}: {
  value: string;
  label: string;
  count: number;
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `col-${value}` });
  return (
    <div ref={setNodeRef} className={`ev-board__col${isOver ? " ev-board__col--over" : ""}`}>
      <div className="ev-board__col-head" data-testid={`col-${value === UNSET_COL ? "unset" : value}`}>
        <span className="ev-board__col-name">{label}</span>
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
}) {
  // §E — a read-only member can neither drag the card nor change its status.
  const { attributes, listeners, setNodeRef, transform } = useDraggable({
    id: `card-${entity.number}`,
    disabled: readOnly,
  });
  return (
    <div
      ref={setNodeRef}
      data-testid={`card-${entity.number}`}
      className={`ev-card${readOnly ? " ev-card--readonly" : ""}`}
      style={{ transform: transform ? `translate(${transform.x}px, ${transform.y}px)` : undefined }}
      {...attributes}
      {...listeners}
    >
      <div className="ev-card__title">{fieldText(entity.fields[titleField]) || `#${entity.number}`}</div>
      {badges.length > 0 && (
        <div className="ev-card__badges">
          {badges.map((b) => (
            <CardBadge key={b} name={b} value={entity.fields[b]} spec={roleOf(type, b)} users={users} />
          ))}
        </div>
      )}
      {statusSpec?.values && (
        <div>
          <RoleField
            widget={widgetForRole(statusSpec.role)}
            name={statusSpec.name}
            value={entity.fields[groupField]}
            values={statusSpec.values}
            disabled={busy || readOnly}
            onCommit={(next) => onPatch(entity.number, { [groupField]: next })}
          />
        </div>
      )}
    </div>
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
