/**
 * P7 (plan-view-plugins-pr5-finish): "Save as table" — writes the rows a
 * marking lights, every column, as a new CSV in the workspace
 * (`markings/<name>-<yyyymmdd-hhmm>.csv`). Shared by the marking's header
 * control and its sent chip; each says which view the rows come from and
 * whether it holds the values (the header) or the route reads the sent file
 * (the chip).
 *
 * After a save it says where the file went, as a link that opens it; a refusal
 * (no rows lit, a full workspace, an unreachable sandbox, …) is the route's own
 * sentence, shown here — never a crash.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useOptionalFileService } from "../api/fileService";
import {
  type SavedMarkingTable,
  saveMarkingTable,
  type SaveMarkingTableBody,
  SaveTableRefused,
  tableStamp,
} from "../api/markingTable";
import { useOpenFile, useWorkspaceVisible } from "../hooks/openFile";
import { invalidateTree } from "../hooks/invalidateTree";
import { useWorkspaceSlug } from "../hooks/useWorkspaceSlug";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { Icon } from "./Icon";

export type SaveMarkingTableProps = {
  name: string;
  /** The view the rows come from; `null` → disabled with `why`. */
  view: string | null;
  /** The marking's values, or `null` for the route to read the sent file. */
  columns: Record<string, string[]> | null;
  /** Why the save cannot run now; the button is disabled and says so. */
  why?: string | null;
};

/** The item workspace the save writes into, or null outside one — where
 * nothing is offered, since there is nowhere to write. */
export function useSaveScope(): { slug: string; itemId: string } | null {
  const slug = useWorkspaceSlug();
  const itemId = useOptionalFileService()?.scopeId;
  return slug && itemId ? { slug, itemId } : null;
}

export function SaveMarkingTable(props: SaveMarkingTableProps & { scope: { slug: string; itemId: string } }) {
  const { name, view, columns, why, scope } = props;
  const t = useT();
  const qc = useQueryClient();
  const opener = useOpenFile();
  const openFile = useWorkspaceVisible() ? opener : null;
  const save = useMutation<SavedMarkingTable, Error, SaveMarkingTableBody>({
    mutationFn: (body) => saveMarkingTable(scope.slug, scope.itemId, body),
    onSuccess: () => void invalidateTree(qc, scope.itemId),
  });
  const blocked = why ?? (view ? null : t("markings.noView"));
  const refusal =
    save.error instanceof SaveTableRefused && save.error.message
      ? save.error.message
      : save.error
        ? t("markings.saveFailed")
        : null;
  const saved = save.data;
  const fileName = saved ? saved.path.split("/").pop()! : "";
  return (
    <span
      className="save-marking-table"
      style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap", minWidth: 0 }}
    >
      <button
        type="button"
        className="btn"
        data-size="sm"
        data-variant="secondary"
        disabled={!!blocked || save.isPending}
        title={blocked ?? undefined}
        onClick={() =>
          view &&
          save.mutate({ name, view, columns, stamp: tableStamp(new Date()) })
        }
      >
        <Icon name="download" size={11} />
        {save.isPending ? t("markings.saving") : t("markings.saveTable")}
      </button>
      {saved && !save.isPending && (
        <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)", minWidth: 0, overflowWrap: "anywhere" }}>
          {t(saved.rows === 1 ? "markings.savedOne" : "markings.saved", { n: String(saved.rows) })}{" "}
          {openFile ? (
            <button
              type="button"
              onClick={() => openFile(saved.path)}
              style={{
                background: "none",
                border: "none",
                padding: 0,
                font: "inherit",
                color: "var(--accent)",
                textDecoration: "underline",
                cursor: "pointer",
                overflowWrap: "anywhere",
              }}
            >
              {fileName}
            </button>
          ) : (
            <span>{saved.path.replace(/^\//, "")}</span>
          )}
        </span>
      )}
      {refusal && !save.isPending && (
        <span role="alert" style={{ fontSize: pxToRem(11), color: "var(--err)", overflowWrap: "anywhere" }}>
          {refusal}
        </span>
      )}
    </span>
  );
}
