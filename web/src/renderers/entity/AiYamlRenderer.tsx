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

import { useMemo } from "react";

import type { EntityHealthFinding } from "../../api/entities";
import { useFileService } from "../../api/fileService";
import { useEditMode } from "../../hooks/editMode";
import { useFileBuffer } from "../../hooks/fileBuffer";
import { useOpenFile } from "../../hooks/openFile";
import { useEntities, useEntityCatalog, useEntityHealth, useReferencedRecords } from "../../hooks/useEntities";
import { useEntityLiveSync } from "../../hooks/useEntityLiveSync";
import { useEntityWrite } from "../../hooks/useEntityWrite";
import { useItemCanWrite } from "../../hooks/useItemCanWrite";
import { useUsers } from "../../hooks/useUsers";
import { useWorkspaceSlug } from "../../hooks/useWorkspaceSlug";
import { TextRenderer } from "../TextRenderer";
import { YamlTree } from "../YamlTree";
import { EntityViewBody, HealthView, parseViewSpec } from "./EntityViews";
import { buildRefIndex, referencedTypes } from "./refTraversal";

export function AiYamlRenderer({ path }: { path: string }) {
  const { isEditing } = useEditMode();
  const { entry } = useFileBuffer(path);
  const slug = useWorkspaceSlug();
  const itemId = useFileService().scopeId;

  // Parse the spec from whatever text is loaded; empty (still loading / not a
  // view) yields no entity name, which gates the queries off (`enabled`), so
  // every hook below is still called unconditionally.
  const spec = entry.status === "ready" ? parseViewSpec(entry.text) : null;
  const entityName = spec?.entity ?? "";
  const isHealth = spec?.view === "health";

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

  // Resolve the type + load its referenced types BEFORE the early returns, so the
  // ref-record queries stay unconditional (rules of hooks). `milestone.title`
  // columns + ref pickers read these at render time (§A4).
  const type = catalogQ.data?.types.find((t) => t.name === entityName) ?? null;
  const refTypes = useMemo(() => referencedTypes(type), [type]);
  const refIndex = buildRefIndex(useReferencedRecords(slug, itemId, refTypes));

  if (isEditing(path)) return <TextRenderer path={path} />;
  if (entry.status === "loading") {
    return <div style={{ color: "var(--text-paper-d)" }}>Loading {path}…</div>;
  }
  if (entry.status === "error") {
    return <div style={{ color: "var(--err)" }}>{entry.error ?? "load failed"}</div>;
  }
  if (!spec) return <YamlTree text={entry.text} />;

  if (spec.view === "health") {
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
  // (hence its records_path) is known.
  const onOpenRecord =
    openFile && type ? (number: number) => openFile(`/${type.records_path}/${number}.md`) : undefined;

  return (
    <EntityViewBody
      spec={spec}
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
      onSave={write.save}
      onOpenRecord={onOpenRecord}
      busy={write.isBusy}
      conflicts={write.conflicts}
      onDismissConflict={write.dismissConflict}
    />
  );
}
