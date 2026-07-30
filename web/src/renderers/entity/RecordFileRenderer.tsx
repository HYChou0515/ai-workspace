/**
 * RecordFileRenderer — the file-preview for an entity *record* file (#453, §C2).
 * A record lives as `{records_path}/{N}.md` (e.g. `issues/5.md`), so opening one
 * in the workspace IDE should render the structured file editor, not raw
 * markdown.
 *
 * The registry only hands a renderer `{ path }`, so this container resolves the
 * rest from context — slug + item id, the catalog (to confirm the parent folder
 * IS a records_path), and the projected record — then hands the pure
 * `EntityFileEditor` the record + the shared `useEntityWrite.save` seam (§B1/§B2
 * optimistic + 409-conflict). Anything that isn't a live record degrades to
 * plain markdown so nothing is ever blanked out:
 *   - a numeric `.md` in a folder that isn't a records_path (just a doc named N.md),
 *   - a number with no projected record (an unparseable / stray file, §D),
 *   - the raw byte-edit escape hatch (the tab-strip Edit toggle).
 */

import { useMemo } from "react";

import { useFileService } from "../../api/fileService";
import { useEditMode } from "../../hooks/editMode";
import { useEntities, useEntityCatalog, useReferencedRecords } from "../../hooks/useEntities";
import { useEntityWrite } from "../../hooks/useEntityWrite";
import { useItemCanWrite } from "../../hooks/useItemCanWrite";
import { useUsers } from "../../hooks/useUsers";
import { useWorkspaceSlug } from "../../hooks/useWorkspaceSlug";
import { MarkdownRenderer } from "../MarkdownRenderer";
import { EntityRecordPane } from "./EntityRecordPane";
import { ConflictBanner } from "./shared";
import { buildRefIndex, referencedTypes, refOptionsForField } from "./refTraversal";

/** `{records_path}/{N}.md` → its folder + number, or null if the basename isn't a
 * bare integer `.md`. The folder is normalised (no leading slash) to compare with
 * a type's `records_path`. */
export function recordCoords(path: string): { dir: string; number: number } | null {
  const m = /(?:^|\/)(\d+)\.md$/i.exec(path);
  if (!m) return null;
  const slash = path.lastIndexOf("/");
  const dir = (slash >= 0 ? path.slice(0, slash) : "").replace(/^\/+/, "");
  return { dir, number: Number(m[1]) };
}

const norm = (p: string) => p.replace(/^\/+/, "").replace(/\/+$/, "");

function Loading({ path }: { path: string }) {
  return <div style={{ color: "var(--text-paper-d)" }}>Loading {path}…</div>;
}

export function RecordFileRenderer({ path }: { path: string }) {
  const { isEditing } = useEditMode();
  const slug = useWorkspaceSlug();
  const itemId = useFileService().scopeId;
  const coords = recordCoords(path);

  // Resolve the owning type from the catalog BEFORE any early return so the list
  // query below stays unconditional (rules of hooks); a non-records folder leaves
  // `type` null → the list query is gated off (empty entity name).
  const catalogQ = useEntityCatalog(slug, itemId);
  const type = coords
    ? (catalogQ.data?.types.find((t) => norm(t.records_path) === coords.dir) ?? null)
    : null;
  const entityName = type?.name ?? "";
  const listQ = useEntities(slug, itemId, entityName);
  // §E read-only gate — same wiring as AiYamlRenderer: a read-only member's
  // editor renders disabled instead of offering a Save that 403s.
  const canWrite = useItemCanWrite(slug, itemId);
  const write = useEntityWrite(slug, itemId, entityName, { canWrite });
  const users = useUsers();
  // Load the records this type's `ref` fields point at (e.g. milestones) so the
  // editor can offer a #N-title picker instead of a raw number box (§A4). Hooks
  // stay unconditional — an empty refTypes list gates the queries off.
  const refTypes = useMemo(() => referencedTypes(type), [type]);
  const refIndex = buildRefIndex(useReferencedRecords(slug, itemId, refTypes));

  // The tab-strip Edit toggle is the raw full-file escape hatch (fix a record the
  // structured editor can't express, or a plain doc) — hand off to markdown/Monaco.
  if (isEditing(path)) return <MarkdownRenderer path={path} />;
  // Registry gate should guarantee a match, but be defensive.
  if (!coords) return <MarkdownRenderer path={path} />;
  if (catalogQ.isLoading) return <Loading path={path} />;
  // The folder isn't a records_path → it's a doc that merely looks like `N.md`.
  if (!type) return <MarkdownRenderer path={path} />;
  if (listQ.isLoading) return <Loading path={path} />;

  const record = listQ.data?.entities.find((e) => e.number === coords.number) ?? null;
  // No projected record for this number (unparseable / stray file) → don't blank
  // out; show the raw markdown so the user can still read + repair it (§D).
  if (!record) return <MarkdownRenderer path={path} />;

  const inConflict = write.conflicts.includes(record.number);

  return (
    <div style={{ height: "100%", overflow: "auto" }}>
      {inConflict && <ConflictBanner conflicts={[record.number]} onDismiss={write.dismissConflict} />}

      <EntityRecordPane
        // The whole IDE mounts ONE <FileView>, so switching tabs only swaps the
        // `path` prop and this pane is reused in place. Both the form (seeded
        // from `record` via useState, run once on mount) and the view/edit mode
        // would otherwise follow you across tabs — opening issues/2.md after
        // editing issues/1.md showed #1's title/date, and #1's edit mode. Key it
        // to the path so a different record file remounts, re-seeds, and opens
        // in its own reading state. (The pane is shared with the #680 modal, which
        // gets the same freshness for free: it mounts per open.)
        key={path}
        path={path}
        type={type}
        record={record}
        users={users}
        canWrite={write.canWrite}
        busy={write.isBusy}
        refOptionsFor={(name) => refOptionsForField(type, refIndex, name)}
        onSave={(patch, body) => write.save(record.number, patch, body)}
      />
    </div>
  );
}
