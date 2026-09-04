/**
 * Shared body for the "delete this item and everything it owns" dialog
 * (plan-delete-item-cascade). ONE copy of the copy: the chat rail and
 * My resources both render this, so the two dialogs cannot drift — and both
 * honour the locked decisions the plan recorded: the live usage number
 * (what the delete buys back) and the zip escape hatch (the last chance to
 * keep the bytes, since the cascade has no undo).
 */

import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { API_PREFIX, apiFetch } from "../api/http";
import { qk } from "../api/queryKeys";
import { formatBytes } from "../lib/bytes";
import { useT } from "../lib/i18n";

export function DeleteItemBody({ slug, itemId }: { slug: string; itemId: string }) {
  const t = useT();
  // Best-effort: the dialog must not block on (or die with) the usage call —
  // no number shown beats no dialog.
  const { data: usage } = useQuery({
    queryKey: qk.workspaceUsage(slug, itemId),
    queryFn: () => api.getWorkspaceUsage(slug, itemId),
    retry: false,
  });
  return (
    <>
      <p>{t("resources.disk.delete.body1")}</p>
      {usage != null && usage.used > 0 ? (
        <p>{t("resources.disk.delete.usage", { bytes: formatBytes(usage.used) })}</p>
      ) : null}
      <p>
        {t("resources.disk.delete.body2")}{" "}
        <button
          type="button"
          className="btn"
          // A variant is what gives `.btn` its chrome (base.css sets a
          // TRANSPARENT border on the bare class) — without it the escape
          // hatch rendered as plain text and read as unpressable. Caught by
          // recording the demo, not by a test.
          data-variant="secondary"
          data-size="sm"
          onClick={() => void downloadItemZip(slug, itemId)}
        >
          {t("resources.disk.delete.zip")}
        </button>
      </p>
    </>
  );
}

/** Prepare a whole-workspace zip and hand it to the browser — the same
 * prepare→stream flow the file tree's folder download uses (#247). */
export async function downloadItemZip(slug: string, itemId: string): Promise<void> {
  const base = `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/files/download`;
  const resp = await apiFetch(`${base}/prepare`, { method: "POST" });
  if (!resp.ok) throw new Error(`prepare zip failed: ${resp.status}`);
  const prep = (await resp.json()) as { download_id: string; filename: string };
  const a = document.createElement("a");
  a.href = `${API_PREFIX}${base}/${encodeURIComponent(prep.download_id)}`;
  a.download = prep.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
