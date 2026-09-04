/**
 * AiYamlRenderer — the file-preview for a `views/*.ai.yaml` entity view (#419).
 * Registered in the renderer registry ahead of the generic YAML tree, so opening
 * a view file in the workspace IDE renders the live board / table / gantt instead
 * of raw YAML.
 *
 * The registry only hands a renderer `{ path }`, so this container resolves the
 * rest from context — slug (`useWorkspaceSlug`), item id (`useFileService`),
 * and the view spec (parsed from the file buffer) — then runs the entity queries
 * + wires the create / update write path, handing everything to the pure
 * `EntityViewBody`. A non-view `.ai.yaml` (or malformed one) degrades to the
 * structured YAML tree; editing flips to the raw byte editor like every other
 * structured preview (§E, #361).
 */

import { useMemo, useState } from "react";

import { dump, load } from "js-yaml";

import type { EntityHealthFinding } from "../../api/entities";
import { useFileService } from "../../api/fileService";
import { useEditMode } from "../../hooks/editMode";
import { useFileBuffer } from "../../hooks/fileBuffer";
import { useOpenFile } from "../../hooks/openFile";
import { useRefreshFiles } from "../../hooks/useRefreshFiles";
import { useEntities, useEntityCatalog, useEntityHealth, useReferencedRecords } from "../../hooks/useEntities";
import { useEntityLiveSync } from "../../hooks/useEntityLiveSync";
import { useEntityWrite } from "../../hooks/useEntityWrite";
import { useItemCanWrite } from "../../hooks/useItemCanWrite";
import { useUsers } from "../../hooks/useUsers";
import { useWorkspaceSlug } from "../../hooks/useWorkspaceSlug";
import { TextRenderer } from "../TextRenderer";
import { WuiView } from "../wui/WuiView";
import { YamlTree } from "../YamlTree";
import { EntityRecordModal } from "./EntityRecordModal";
import { EntityViewBody, HealthView, parseViewSpec } from "./EntityViews";
import { buildRefIndex, referencedTypes, refOptionsForField } from "./refTraversal";
import { setViewScalar } from "./shared";
import { VIEW_KIND, type SortRule, type ViewConfig } from "./types";
import { ViewErrorBoundary } from "./ViewErrorBoundary";

/** The View panel's uncommitted edits — a full snapshot of the three panel-owned
 * spec fields (so an untouched field persists through a save of the others). */
type ViewOverride = { group_by: string; sort: SortRule[]; hidden_fields: string[] };

export function AiYamlRenderer({ path }: { path: string }) {
  const { isEditing } = useEditMode();
  const { entry, applyExternalWrite } = useFileBuffer(path);
  const slug = useWorkspaceSlug();
  const fileService = useFileService();
  const itemId = fileService.scopeId;

  // Parse the spec from whatever text is loaded; empty (still loading / not a
  // view) yields no entity name, which gates the queries off (`enabled`), so
  // every hook below is still called unconditionally.
  const spec = entry.status === "ready" ? parseViewSpec(entry.text) : null;
  const entityName = spec?.entity ?? "";
  const isHealth = spec?.view === VIEW_KIND.health;

  // §E read-only gate: derive the item's write permission for this member; a
  // read-only viewer's write affordances are hidden and every write is a no-op.
  const canWrite = useItemCanWrite(slug, itemId);

  // §C3/§E live-sync: while a view is open, refetch on a peer's / agent's entity
  // write (broadcast as `file_changed` on the item stream).
  useEntityLiveSync(slug, itemId, !!spec);

  const catalogQ = useEntityCatalog(slug, itemId);
  const listQ = useEntities(slug, itemId, entityName);
  const healthQ = useEntityHealth(slug, itemId, isHealth);
  const write = useEntityWrite(slug, itemId, entityName, { canWrite });
  const users = useUsers();
  const openFile = useOpenFile();
  // #698 — the boundary's Retry: a plug-in reads FILES, so re-render alone
  // can't help when the repair happened outside this tab. Re-read first.
  const refreshFiles = useRefreshFiles(itemId);

  // Resolve the type + load its referenced types BEFORE the early returns, so the
  // ref-record queries stay unconditional (rules of hooks). `milestone.title`
  // columns + ref pickers read these at render time (§A4).
  const type = catalogQ.data?.types.find((t) => t.name === entityName) ?? null;
  const refTypes = useMemo(() => referencedTypes(type), [type]);
  const refIndex = buildRefIndex(useReferencedRecords(slug, itemId, refTypes));
  // #PM auto-schedule — an `auto` milestone's own span is worked out from its
  // issues, and a milestone is a DIFFERENT entity type, so it needs its own
  // write hook. Empty name ⇒ gated off, which keeps the hook unconditional for
  // every view that schedules nothing.
  const anchorField = spec?.schedule?.anchor;
  const anchorType = anchorField ? (type?.fields.find((f) => f.name === anchorField)?.to ?? "") : "";
  const anchorWrite = useEntityWrite(slug, itemId, anchorType, { canWrite });

  // #680 — which record is open in the modal (null ⇒ none). Held here, not in a
  // renderer, so all three view kinds open the SAME modal and it survives a
  // re-render of the chart underneath it.
  const [openRecord, setOpenRecord] = useState<number | null>(null);

  // #GH-projects P3 — the "View" panel's uncommitted edits (Group by / Sort /
  // Fields). `override` (undefined ⇒ untouched) applies locally; once saved the
  // choice lives in the file buffer (see saveView), so the committed values are
  // just the parsed spec. Hooks stay unconditional (before the early returns).
  const [override, setOverride] = useState<ViewOverride | undefined>(undefined);
  const [saving, setSaving] = useState(false);
  // The IDE mounts ONE <FileView> and only swaps the `path` prop on a file
  // switch, so this renderer instance is REUSED across view files (same as the
  // record editor — see RecordFileRenderer's key={path}). Reset the uncommitted
  // `override` the moment `path` changes, or the previous view's unsaved config
  // bleeds into the next file. Adjusting state during render (React's documented
  // pattern) resets before commit, so the stale config never paints. (The SAVED
  // config can't bleed: it's per-path in the file buffer, not here.)
  const [statePath, setStatePath] = useState(path);
  if (path !== statePath) {
    setStatePath(path);
    setOverride(undefined);
    setSaving(false);
    // A record opened from the previous view must not hang over the next one —
    // its number means something different under a different entity type.
    setOpenRecord(null);
  }

  if (isEditing(path)) return <TextRenderer path={path} />;
  if (entry.status === "loading") {
    return <div style={{ color: "var(--text-paper-d)" }}>Loading {path}…</div>;
  }
  if (entry.status === "error") {
    return <div style={{ color: "var(--err)" }}>{entry.error ?? "load failed"}</div>;
  }
  if (!spec) return <YamlTree text={entry.text} />;

  // A WUI is the view file's FOLDER, run as a page — so it is rendered here,
  // ahead of the dispatcher, for the two things the dispatcher would take away:
  // the file's own path (which is how the folder is found) and the whole pane
  // (the entity panel's title bar and empty-state chrome mean nothing here).
  if (spec.view === VIEW_KIND.wui) return <WuiView path={path} spec={spec} />;

  if (spec.view === VIEW_KIND.health) {
    // Click-to-fix: resolve the finding's type → `records_path` from the catalog
    // and open its record file. Only offered when a shell publishes an opener
    // (§F, #454); an unknown type just no-ops rather than opening a bad path.
    const onJump = openFile
      ? (finding: EntityHealthFinding) => {
          const t = catalogQ.data?.types.find((ty) => ty.name === finding.type_name);
          if (t) openFile(`/${t.records_path}/${finding.number}.md`);
        }
      : undefined;
    return <HealthView title={spec.title} findings={healthQ.data?.findings ?? []} onJump={onJump} />;
  }

  const list = listQ.data;

  // #4 — a board card's ⋯ menu opens the record's `{records_path}/N.md` file in a
  // new tab; only offered when a shell publishes an opener (§F, #454) and the type
  // (hence its records_path) is known. Distinct from the #680 modal below: this
  // one LEAVES the view, and is the only route to the raw whole-file editor.
  const onOpenRecordFile =
    openFile && type ? (number: number) => openFile(`/${type.records_path}/${number}.md`) : undefined;

  // #680 — the in-place route: a bar / row / card double-click. Needs no opener
  // (nothing is navigated) but does need a schema to render the record's fields,
  // so an unknown type leaves the gesture inert rather than opening a blank shell.
  const openInModal = type ? (number: number) => setOpenRecord(number) : undefined;
  const modalRecord = openRecord === null ? null : (list?.entities.find((e) => e.number === openRecord) ?? null);

  // #GH-projects P3 — the effective (locally-overridden) View config drives the
  // view; "Save to view" serialises Group by / Sort / Fields into the `.ai.yaml`.
  const fields = type?.fields ?? [];
  const specGroupBy = spec.group_by ?? "";
  const specSort = spec.sort ?? [];
  const specHidden = spec.hidden_fields ?? [];
  const eff = {
    group_by: override?.group_by ?? specGroupBy,
    sort: override?.sort ?? specSort,
    hidden_fields: override?.hidden_fields ?? specHidden,
  };
  const sameList = (a: string[], b: string[]) =>
    a.length === b.length && [...a].sort().join("\0") === [...b].sort().join("\0");
  const dirty =
    override !== undefined &&
    (eff.group_by !== specGroupBy ||
      JSON.stringify(eff.sort) !== JSON.stringify(specSort) ||
      !sameList(eff.hidden_fields, specHidden));
  // Every panel edit snapshots the three fields (so the untouched two persist) and
  // applies the change; `patch` seeds from the SPEC on first touch.
  const patch = (p: Partial<ViewOverride>) =>
    setOverride((o) => ({ group_by: specGroupBy, sort: specSort, hidden_fields: specHidden, ...o, ...p }));

  const effectiveSpec = {
    ...spec,
    group_by: eff.group_by || undefined,
    sort: eff.sort.length ? eff.sort : undefined,
    hidden_fields: eff.hidden_fields.length ? eff.hidden_fields : undefined,
  };

  // Candidate columns (mirrors TableView.columnsFor): explicit `columns`, else the
  // schema fields minus the manual-order `rank`.
  const candidateColumns =
    effectiveSpec.columns && effectiveSpec.columns.length > 0
      ? effectiveSpec.columns
      : fields.filter((f) => f.role !== "rank").map((f) => f.name);
  const groupOptions = fields
    .filter((f) => f.role === "status" || f.role === "actor" || f.role === "ref")
    .map((f) => ({ name: f.name, label: f.name }));
  // #690 P3 — what a bar's colour may mean. Select-ish fields and people: both
  // give the chart a stable string to colour from, though not the same palette
  // — a `status` takes the chip slots so it matches its chip in the table, and
  // an `actor` takes a generated hue per person, since six slots cannot hold a
  // directory (actorColor.ts). `ref` is left out until it can resolve its
  // display value (plan §7 R3).
  const colorByOptions = fields
    .filter((f) => f.role === "status" || f.role === "actor")
    .map((f) => ({ name: f.name, label: f.name }));
  const sortOptions = fields
    .filter((f) => f.role !== "rank" && f.role !== "backref")
    .map((f) => ({ name: f.name, label: f.name }));

  const saveView = async () => {
    if (entry.status !== "ready") return;
    setSaving(true);
    try {
      const obj = (load(entry.text) ?? {}) as Record<string, unknown>;
      if (eff.group_by) obj.group_by = eff.group_by;
      else delete obj.group_by;
      if (eff.sort.length) obj.sort = eff.sort;
      else delete obj.sort;
      if (eff.hidden_fields.length) obj.hidden_fields = eff.hidden_fields;
      else delete obj.hidden_fields;
      const yaml = dump(obj);
      await fileService.writeFile(path, yaml);
      // Reflect the just-written YAML in THIS path's buffer, so the committed
      // config now IS the parsed spec: it survives a switch away and back (the
      // buffer won't re-fetch an already-loaded path) and stays per-file (no
      // cross-view bleed) without a per-instance bridge.
      applyExternalWrite(yaml);
      setOverride(undefined);
    } finally {
      setSaving(false);
    }
  };

  // Write a comment-safe YAML edit back to this view file (gantt gear toggles).
  const persistGantt = (yaml: string) => void fileService.writeFile(path, yaml).then(() => applyExternalWrite(yaml));

  const viewConfig: ViewConfig | undefined =
    canWrite && type && (spec.view === VIEW_KIND.table || spec.view === VIEW_KIND.board)
      ? {
          fieldOptions: candidateColumns.map((c) => ({ name: c, label: c })),
          hidden: eff.hidden_fields,
          onToggleField: (name) =>
            patch({
              hidden_fields: eff.hidden_fields.includes(name)
                ? eff.hidden_fields.filter((x) => x !== name)
                : [...eff.hidden_fields, name],
            }),
          groupBy: eff.group_by,
          groupOptions,
          onGroupBy: (field) => patch({ group_by: field }),
          sort: eff.sort,
          sortOptions,
          onSetSort: (rules) => patch({ sort: rules }),
          dirty,
          saving,
          onSave: () => void saveView(),
          onReset: () => setOverride(undefined),
        }
      : canWrite && spec.view === VIEW_KIND.gantt
        ? // gantt gear: Group by + working-day toggle + people display. Each change
          // persists comment-safe via a targeted text edit, NOT saveView's js-yaml
          // dump, so the self-documenting `week:` block survives. (Sort / Fields
          // don't apply to a bar chart, so those sections stay empty → hidden.)
          {
            fieldOptions: [],
            hidden: [],
            onToggleField: () => {},
            groupBy: eff.group_by,
            groupOptions,
            onGroupBy: (field) => persistGantt(setViewScalar(entry.text, "group_by", field || null)),
            sort: spec.sort ?? [],
            sortOptions,
            // sort is a list — persist it as inline flow YAML (valid, comment-safe),
            // or drop the line when empty (back to manual rank / drag order).
            onSetSort: (rules) => persistGantt(setViewScalar(entry.text, "sort", rules.length ? JSON.stringify(rules) : null)),
            skipWeekends: spec.skip_weekends ?? false,
            onToggleSkipWeekends: (next) => persistGantt(setViewScalar(entry.text, "skip_weekends", String(next))),
            colorBy: spec.color_by ?? "",
            colorByOptions,
            onSetColorBy: (field) => persistGantt(setViewScalar(entry.text, "color_by", field || null)),
            // The time-axis settings only appear WITH a week rule: without one
            // the axis has no week code to show or keep, so all three would be
            // knobs wired to nothing.
            ...(spec.week
              ? {
                  alwaysWeek: spec.always_week ?? false,
                  onToggleAlwaysWeek: (next: boolean) =>
                    persistGantt(setViewScalar(entry.text, "always_week", next ? "true" : null)),
                  weekday: spec.weekday ?? "number",
                  onSetWeekday: (format: string) => persistGantt(setViewScalar(entry.text, "weekday", format)),
                  dayOfMonth: spec.day_of_month ?? "hidden",
                  onSetDayOfMonth: (mode: string) => persistGantt(setViewScalar(entry.text, "day_of_month", mode)),
                }
              : {}),
            assigneeDisplay: spec.assignee_display ?? "avatar",
            onSetAssigneeDisplay: (mode) => persistGantt(setViewScalar(entry.text, "assignee_display", mode)),
            dirty: false,
            onSave: () => {},
            onReset: () => {},
          }
        : undefined;

  return (
    <>
      {/* The boundary wraps the WHOLE panel, not just the registered component:
          the header renders `spec.title`, which is where a hostile `.ai.yaml`
          crashed the app before the parser started coercing. Wrapping only the
          component left that site — the original one — outside the net.
          `resetKey` covers both the file and its contents, so switching tabs or
          repairing the file in place both get a fresh attempt. */}
      <ViewErrorBoundary kind={spec.view} resetKey={`${path}\n${entry.text}`} onRetry={refreshFiles}>
        <EntityViewBody
            spec={effectiveSpec}
          // #690 P4 — per project, per view: the collapse state lives in this
          // person's browser, and two views must not collapse each other.
          viewKey={`${itemId}:${path}`}
          type={type}
          entities={list?.entities ?? []}
          invalid={list?.invalid ?? []}
          users={users}
          refIndex={refIndex}
          canWrite={canWrite}
          catalogDiagnostics={catalogQ.data?.diagnostics ?? []}
          // catalog loaded but this type isn't in it → its schema failed to load (§D).
          schemaMissing={catalogQ.isSuccess && !type}
          onCreate={write.create}
          onPatch={write.patch}
          onPatchAnchor={anchorType ? anchorWrite.patch : undefined}
          onOpenRecord={openInModal}
          onOpenRecordFile={onOpenRecordFile}
          busy={write.isBusy}
          conflicts={write.conflicts}
          onDismissConflict={write.dismissConflict}
          viewConfig={viewConfig}
        />
      </ViewErrorBoundary>
      {/* #680 — the record a gesture opened. A number with no record behind it
          (deleted by a peer, or dropped from the projection) closes rather than
          rendering an empty shell; the live-sync refetch is what surfaces it. */}
      {type && modalRecord && (
        <EntityRecordModal
          type={type}
          record={modalRecord}
          users={users}
          canWrite={canWrite}
          busy={write.isBusy}
          conflicts={write.conflicts}
          onDismissConflict={write.dismissConflict}
          refOptionsFor={(name) => refOptionsForField(type, refIndex, name)}
          onSave={(patch, body) => write.save(modalRecord.number, patch, body)}
          onClose={() => setOpenRecord(null)}
        />
      )}
    </>
  );
}
