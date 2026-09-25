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
import { VIEW_KIND, type EntityViewProps, type ViewSpec } from "./types";

/** What a kind's `Thumbnail` is handed (#847/#848 P6). */
export type ViewThumbnailProps = {
  /** The parsed view file, as the live view gets it. */
  spec: ViewSpec;
  /** The view file's workspace path. */
  path: string;
  /** Call when there is nothing to draw (a spec that does not fit, a sandbox
   * that said no): the host then shows its plain file card instead. Throwing
   * while rendering does the same. */
  onFail: (reason: string) => void;
};

export type ViewRenderer = {
  kind: string;
  Component: ComponentType<EntityViewProps>;
  /** The renderer draws its own empty state (so the dispatcher shouldn't show
   * the generic "no records yet" placeholder). */
  ownsEmptyState?: boolean;
  /** The renderer has no header quick-create affordance. */
  suppressQuickCreate?: boolean;
  /** #847 PR 3 — the kind takes part in named markings (reads `marking` from
   * its props), so every view of it gets the header's marking control, even one
   * whose file names neither `marking:` nor `keys:`. Omitted: only such a
   * view that names one of them gets it. Additive to SDK 1. */
  linkable?: boolean;
  /** #698 — the kind draws entity records, so its view file MUST name an
   * `entity:`; the dispatcher says so visibly when it doesn't. Omitted ≡ false,
   * which is what a plug-in reading workspace files wants: it has no entity, and
   * requiring one made such a kind unrepresentable. Whether a kind needs an
   * entity is a property OF THE KIND, so it lives here and nowhere else — the
   * parser used to hardcode `health` as the lone exception. */
  needsEntity?: boolean;
  /** #847/#848 P6 — a small, static drawing of a view of this kind, for the
   * chat card of a file the agent showed. It fills the box it is given, draws
   * once, and takes no input: the host puts it inside the card's own click
   * target (which opens the live view) with pointer events off, and mounts it
   * only once the card has scrolled into view. Omitted: the card stays a plain
   * file card. Additive to SDK 1. */
  Thumbnail?: ComponentType<ViewThumbnailProps>;
};

/** Mutable so a second-party module can register on import (#698). Keyed by
 * kind, so a name clash is a plain collision we can refuse outright. */
const registry = new Map<string, ViewRenderer>();

/** Names the CONTAINER answers to before the dispatcher ever runs, so no entry
 * here can win them. Without this, registering `health` succeeded and produced a
 * component that simply never rendered — the exact silent outcome the duplicate
 * check exists to prevent, on the one built-in name a plug-in might reuse. */
const RESERVED = new Set<string>([VIEW_KIND.health, VIEW_KIND.wui]);

/** Add a view kind. Throws on a name that is taken rather than silently
 * replacing the incumbent — two renderers answering to one `view:` has no right
 * answer, and a silent winner would depend on import order. */
export function registerViewKind(def: ViewRenderer): void {
  if (!def.kind) {
    // `parseViewSpec` requires a non-empty `view:`, so an empty name registers
    // fine and can never match — the same silent outcome RESERVED exists for.
    throw new Error("a view kind needs a name — an empty one can never match a view file");
  }
  if (RESERVED.has(def.kind)) {
    throw new Error(`view kind "${def.kind}" is reserved by the platform — pick a different name`);
  }
  if (registry.has(def.kind)) {
    throw new Error(`view kind "${def.kind}" is already registered — pick a different name`);
  }
  registry.set(def.kind, def);
}

/** Whether a kind is registered. For the runtime plugin loader (#847/#848) —
 * not re-exported from `public.ts`: a plug-in has no reason to ask. */
export function hasViewKind(kind: string): boolean {
  return registry.has(kind);
}

/** Remove a kind. Test-only seam, so the module-level registry doesn't leak
 * between cases — deliberately NOT re-exported from `public.ts`, or the
 * duplicate check above would be opt-out for the very code it guards against. */
export function unregisterViewKind(kind: string): void {
  registry.delete(kind);
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

registerViewKind({ kind: VIEW_KIND.table, Component: TableView, needsEntity: true });
registerViewKind({ kind: VIEW_KIND.board, Component: BoardView, needsEntity: true });
registerViewKind({
  kind: VIEW_KIND.gantt,
  Component: GanttView,
  needsEntity: true,
  ownsEmptyState: true,
  // + New is offered (via the shared modal) so a gantt-only entity — the
  // Roadmap is a gantt of milestones — still has a way to add records. It used
  // to be suppressed only because the OLD inline create form was awkward here.
});
