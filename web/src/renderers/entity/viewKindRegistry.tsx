/**
 * view-kind → renderer registry (#448 P1, opened to plug-ins in #698).
 *
 * A view kind is a name a `*.ai.yaml` file asks for via `view:`. This module is
 * the ONE place that knows which kinds exist — `parseViewSpec` no longer keeps a
 * copy of the list, so a kind can be added without touching the parser, the
 * dispatcher, or a TypeScript union.
 *
 * Registering is a plain call, and the built-ins below go through the SAME
 * public function a second-party kind uses — there is no privileged path, so the
 * route a plug-in takes is the one exercised on every startup.
 *
 * An unknown kind resolves to a non-fatal fallback notice rather than throwing,
 * so a view file naming a not-yet-registered kind degrades gracefully (§D).
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
  /** #698 — the kind draws entity records, so its view file MUST name an
   * `entity:`; the dispatcher says so visibly when it doesn't. Omitted ≡ false,
   * which is what a plug-in reading workspace files wants: it has no entity, and
   * requiring one made such a kind unrepresentable. Whether a kind needs an
   * entity is a property OF THE KIND, so it lives here and nowhere else — the
   * parser used to hardcode `health` as the lone exception. */
  needsEntity?: boolean;
};

/** Mutable so a second-party module can register on import (#698). Keyed by
 * kind, so a name clash is a plain collision we can refuse outright. */
const registry = new Map<string, ViewRenderer>();

/** Add a view kind. Throws on a duplicate name rather than silently replacing
 * the incumbent — two renderers answering to one `view:` has no right answer,
 * and a silent winner would depend on import order. */
export function registerViewKind(def: ViewRenderer): void {
  if (registry.has(def.kind)) {
    throw new Error(`view kind "${def.kind}" is already registered — pick a different name`);
  }
  registry.set(def.kind, def);
}

/** Remove a kind. Symmetric with `registerViewKind`; tests use it so the
 * module-level registry doesn't leak between cases. */
export function unregisterViewKind(kind: string): void {
  registry.delete(kind);
}

export function hasViewKind(kind: string): boolean {
  return registry.has(kind);
}

/** The registered renderer, or undefined for an unknown kind. Callers that want
 * to RENDER should use `resolveViewRenderer` (which degrades); this one is for
 * asking about a kind's properties (e.g. `needsEntity`). */
export function lookupViewKind(kind: string): ViewRenderer | undefined {
  return registry.get(kind);
}

/** Every registered kind, for diagnostics / docs. */
export function viewKindNames(): string[] {
  return [...registry.keys()];
}

function FallbackView({ spec }: EntityViewProps) {
  return (
    <div style={{ padding: 12, color: "var(--warn)" }}>
      Unsupported view kind: {spec.view}
    </div>
  );
}

/** Resolve a view kind to its renderer, or a graceful fallback for unknown
 * kinds (§D — a bad/unsupported kind degrades, never crashes the panel). */
export function resolveViewRenderer(kind: string): ViewRenderer {
  return (
    registry.get(kind) ?? {
      kind,
      Component: FallbackView,
      ownsEmptyState: true,
      suppressQuickCreate: true,
    }
  );
}

// ── the built-in kinds ─────────────────────────────────────────────────────
// Registered through the public function, exactly as a plug-in is. `health` is
// cross-type and rendered by the container ahead of the dispatcher, so it isn't
// a registry entry.

registerViewKind({ kind: "table", Component: TableView, roleKeys: ["columns"], needsEntity: true });
registerViewKind({ kind: "board", Component: BoardView, roleKeys: ["group_by", "card"], needsEntity: true });
registerViewKind({
  kind: "gantt",
  Component: GanttView,
  roleKeys: ["span", "label", "group_by"],
  needsEntity: true,
  ownsEmptyState: true,
  // + New is offered (via the shared modal) so a gantt-only entity — the
  // Roadmap is a gantt of milestones — still has a way to add records. It used
  // to be suppressed only because the OLD inline create form was awkward here.
});
