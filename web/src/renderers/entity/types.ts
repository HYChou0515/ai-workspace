/**
 * Shared types for the declarative entity view renderers (#419 §B / #448 P1).
 * Extracted into their own module so each view kind (`TableView`, `BoardView`,
 * `GanttView`, `HealthView`) and the `viewKindRegistry` can share them without a
 * circular import back through the `EntityViews` barrel.
 */

import type { EntityInstance, EntityType } from "../../api/entities";
import type { User } from "../../api/types";
import type { DayOfMonth, WeekdayFormat, WeekRule } from "./ganttScale";
import type { ScheduleFields } from "./schedule";
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
  /** #690 — which field decides a bar's colour. Absent ⇒ bars keep the single
   * default colour, so adding this changed no existing view's appearance.
   * Written back when the user picks one in the toolbar, like sort. */
  color_by?: string;
  /** #GH-projects — the multi-level sort. Empty / absent ⇒ manual `rank` order
   * (drag-to-reorder). A non-empty list takes over and disables manual drag. */
  sort?: SortRule[];
  span?: string;
  label?: string;
  /** gantt: an `actor`-role field whose avatar is shown on each bar — "who is
   * responsible" at a glance, the standard PM-tool convention. */
  assignee?: string;
  /** gantt: how the `assignee` shows on each bar — `avatar` (default, round
   * photo/initials), `name` (full name text), or `none` (hidden). */
  assignee_display?: "avatar" | "name" | "none";
  /** gantt only — a custom (non-ISO) week-numbering rule for the time axis.
   * Read verbatim off the view file; omit to keep plain day/month labels. */
  week?: WeekRule;
  /** Axis settings (#690 P8). They live on the view rather than in the app's
   * manifest for the same reason `color_by` does: they answer "what is this
   * tab for", and the people sharing a project share the answer. */
  always_week?: boolean;
  weekday?: WeekdayFormat;
  day_of_month?: DayOfMonth;
  /** gantt only — collapse Saturdays/Sundays so the timeline shows only working
   * days (bars, axis, drag all count Mon–Fri). Default off. */
  skip_weekends?: boolean;
  /** gantt only — which fields carry the schedule, so the Timeline can lay the
   * work out on demand. Present ⇒ the chart offers Recalculate; absent ⇒ it is
   * a plain drawing of dates, as before. Named here rather than assumed because
   * this renderer serves every app's entity types. */
  schedule?: ScheduleFields;
  card?: { title?: string; badges?: string[] };
};

/** The View settings panel's model (#GH-projects P3) — the effective, locally
 * overridden view config the "View" gear popover edits (Fields show/hide, Group
 * by, multi-level Sort), plus save/reset. AiYamlRenderer builds it; every change
 * applies immediately (local), and "Save to view" persists it into the YAML. */
export type ViewConfig = {
  /** Candidate columns, in display order, each toggleable. */
  fieldOptions: { name: string; label: string }[];
  hidden: string[];
  onToggleField: (name: string) => void;
  /** "" = no grouping. */
  groupBy: string;
  groupOptions: { name: string; label: string }[];
  onGroupBy: (field: string) => void;
  sort: SortRule[];
  sortOptions: { name: string; label: string }[];
  onSetSort: (rules: SortRule[]) => void;
  /** gantt only — the working-day (skip weekends) toggle. Present ⇒ the panel
   * shows a "Working days" section; toggling persists straight to the view file. */
  skipWeekends?: boolean;
  onToggleSkipWeekends?: (next: boolean) => void;
  /** gantt only — which field a bar's colour means. "" = off, the single
   * default colour bars had before. Present ⇒ the panel shows a "Colour"
   * section; changing it persists to the view file, because what the chart is
   * ASKING is a thing the project shares. (Where somebody is LOOKING — which
   * groups they collapsed — deliberately does not go here; see GanttView.) */
  colorBy?: string;
  colorByOptions?: { name: string; label: string }[];
  onSetColorBy?: (field: string) => void;
  /** gantt only — how each bar shows its assignee (avatar | name | none). Present
   * ⇒ the panel shows a "People" section; changing it persists to the view file. */
  assigneeDisplay?: string;
  onSetAssigneeDisplay?: (mode: string) => void;
  dirty: boolean;
  saving?: boolean;
  onSave: () => void;
  onReset: () => void;
};

export type EntityViewProps = {
  spec: ViewSpec;
  /** #690 P4 — identifies this view for per-user, per-view UI state kept in the
   * browser (which groups are collapsed). Not in the view FILE: where somebody
   * is looking is not a decision to make for the rest of the project. */
  viewKey?: string;
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
  /** Patch a record of the type the schedule's `anchor` ref points at — a
   * milestone whose own span the schedule works out. Absent ⇒ that type is not
   * writable from here and its span is left alone. */
  onPatchAnchor?: (number: number, patch: Record<string, unknown>) => void;
  /** #680 — open one record in the shared modal, in place, without leaving the
   * view (a bar / row / card double-click). Undefined ≡ the gesture is inert, so
   * a view rendered outside a container that owns the modal stays harmless. */
  onOpenRecord?: (number: number) => void;
  /** #4 — open a record's `{records_path}/N.md` file in a new tab (board card
   * ⋯ → Open file). A DIFFERENT destination from `onOpenRecord`: the file tab
   * carries the raw whole-file escape hatch the modal deliberately doesn't.
   * Undefined when no shell opener is wired ≡ action hidden. */
  onOpenRecordFile?: (number: number) => void;
  busy?: boolean;
};
