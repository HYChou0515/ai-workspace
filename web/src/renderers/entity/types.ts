/**
 * Shared types for the declarative entity view renderers (#419 §B / #448 P1).
 * Extracted into their own module so each view kind (`TableView`, `BoardView`,
 * `GanttView`, `HealthView`) and the `viewKindRegistry` can share them without a
 * circular import back through the `EntityViews` barrel.
 */

import type { EntityInstance, EntityType } from "../../api/entities";
import type { User } from "../../api/types";

export type ViewKind = "table" | "board" | "gantt" | "health";

export type ViewSpec = {
  view: ViewKind;
  entity: string;
  title?: string;
  columns?: string[];
  group_by?: string;
  span?: string;
  label?: string;
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
  onCreate: (args: Record<string, unknown>) => void;
  onPatch: (number: number, patch: Record<string, unknown>) => void;
  busy?: boolean;
};
