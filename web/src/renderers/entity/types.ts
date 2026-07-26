/**
 * Shared types for the declarative entity view renderers (#419 §B / #448 P1).
 * Extracted into their own module so each view kind (`TableView`, `BoardView`,
 * `GanttView`, `HealthView`) and the `viewKindRegistry` can share them without a
 * circular import back through the `EntityViews` barrel.
 */

import type { EntityInstance, EntityType } from "../../api/entities";
import type { User } from "../../api/types";
import type { RefIndex } from "./refTraversal";

export type ViewKind = "table" | "board" | "gantt" | "health";

export type SortDir = "asc" | "desc";
/** One tier of a multi-level sort (#GH-projects). Ties fall through to the next
 * rule; the final tie-break is always the manual `rank`. */
export type SortRule = { field: string; dir: SortDir };

export type ViewSpec = {
  view: ViewKind;
  entity: string;
  title?: string;
  columns?: string[];
  /** #GH-projects — fields hidden from the Table via the View panel. Persisted so
   * the choice survives a reload (the standalone Columns toggle was ephemeral). */
  hidden_fields?: string[];
  group_by?: string;
  /** #GH-projects — the multi-level sort. Empty / absent ⇒ manual `rank` order
   * (drag-to-reorder). A non-empty list takes over and disables manual drag. */
  sort?: SortRule[];
  span?: string;
  label?: string;
  /** gantt: an `actor`-role field whose avatar is shown on each bar — "who is
   * responsible" at a glance, the standard PM-tool convention. */
  assignee?: string;
  card?: { title?: string; badges?: string[] };
};

export type EntityViewProps = {
  spec: ViewSpec;
  /** The entity type from the catalog — supplies field roles + the create form.
   * `null` while the catalog is still loading (renders records read-only). */
  type: EntityType | null;
  entities: EntityInstance[];
  /** Records that failed to parse (shown as a degraded warning banner). */
  invalid?: EntityInstance[];
  /** The company directory, for `actor`-role widgets (assignee pickers). */
  users?: User[];
  /** Records of referenced types, for ref-traversal columns + ref pickers (§A4). */
  refIndex?: RefIndex;
  /** #455 read-only gate: false hides + disables every write affordance (inline
   * edit / +New / drag). Omitted ≡ writable. */
  canWrite?: boolean;
  onCreate: (args: Record<string, unknown>) => void;
  onPatch: (number: number, patch: Record<string, unknown>) => void;
  /** #4 — save a record's frontmatter patch + markdown body (the file-editor
   * path). Powers the board card's ⋯ → Edit modal. Omitted ≡ no edit modal. */
  onSave?: (number: number, patch: Record<string, unknown>, body: string) => void;
  /** #4 — open a record's `{records_path}/N.md` file in a new tab (board card
   * ⋯ → Open file). Undefined when no shell opener is wired ≡ action hidden. */
  onOpenRecord?: (number: number) => void;
  busy?: boolean;
};
