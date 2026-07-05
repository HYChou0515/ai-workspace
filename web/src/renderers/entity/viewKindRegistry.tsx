/**
 * view-kind → renderer registry (#448 P1). Each declarative view kind
 * (`table` / `board` / `gantt`; `health` is cross-type and rendered separately)
 * maps to a component that consumes the shared `EntityViewProps`. Adding a new
 * renderer (a future `chart` / `dashboard`) is a one-line registration here plus
 * its own file — the dispatcher (`EntityViewBody`) never grows a new branch.
 *
 * An unknown kind resolves to a non-fatal fallback notice rather than throwing,
 * so a view file that names a not-yet-supported kind degrades gracefully (§D).
 */

import type { ComponentType } from "react";

import { BoardView } from "./BoardView";
import { GanttView } from "./GanttView";
import { TableView } from "./TableView";
import type { EntityViewProps } from "./types";

export type ViewRenderer = {
  kind: string;
  /** Declares which of the view spec's role-bound keys this renderer consumes,
   * for future introspection; the dispatcher doesn't rely on it yet. */
  roleKeys?: string[];
  Component: ComponentType<EntityViewProps>;
  /** The renderer draws its own empty state (so the dispatcher shouldn't show
   * the generic "no records yet" placeholder). */
  ownsEmptyState?: boolean;
  /** The renderer has no header quick-create affordance. */
  suppressQuickCreate?: boolean;
};

function FallbackView({ spec }: EntityViewProps) {
  return (
    <div style={{ padding: 12, color: "var(--warn)" }}>
      Unsupported view kind: {spec.view}
    </div>
  );
}

/** The entity-bound view kinds. A new renderer (future `chart` / `dashboard`)
 * registers one entry here + its own file — the dispatcher never branches. */
export const viewKindRegistry: Record<string, ViewRenderer> = {
  table: { kind: "table", Component: TableView, roleKeys: ["columns"] },
  board: { kind: "board", Component: BoardView, roleKeys: ["group_by", "card"] },
  gantt: {
    kind: "gantt",
    Component: GanttView,
    roleKeys: ["span", "label", "group_by"],
    ownsEmptyState: true,
    suppressQuickCreate: true,
  },
};

/** Resolve a view kind to its renderer, or a graceful fallback for unknown
 * kinds (§D — a bad/unsupported kind degrades, never crashes the panel). */
export function resolveViewRenderer(kind: string): ViewRenderer {
  return (
    viewKindRegistry[kind] ?? {
      kind,
      Component: FallbackView,
      ownsEmptyState: true,
      suppressQuickCreate: true,
    }
  );
}
