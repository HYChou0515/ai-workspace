import { useMutation, useQueryClient } from "@tanstack/react-query";

import { kbApi, type KbApi, type KbCollection } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { useT } from "../../lib/i18n";
import { pxToRem } from "../../lib/pxToRem";

/**
 * "Auto-generate cards" toggle on the collection settings panel (#377). When on,
 * every document auto-generates context-card proposals (and raises clarification
 * questions) as it finishes indexing — via the index-completion digest hook. It's
 * a user-owned setting persisted through the standard `PATCH /collection/{id}`
 * (`auto_digest`), NOT flipped implicitly by the manual "generate cards" action.
 * Reflects `collection.auto_digest`; a change refreshes the collections list.
 */
export function CardGenToggle({
  collection,
  client = kbApi,
}: {
  collection: KbCollection;
  client?: KbApi;
}) {
  const t = useT();
  const qc = useQueryClient();
  const mut = useMutation({
    mutationFn: (next: boolean) => client.updateCollection(collection.resource_id, { auto_digest: next }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
  });

  return (
    <label
      className="kb-autodigest-toggle"
      style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}
    >
      <input
        type="checkbox"
        data-testid="kb-autodigest-toggle"
        checked={collection.auto_digest}
        disabled={mut.isPending}
        onChange={(e) => mut.mutate(e.target.checked)}
      />
      <span style={{ display: "inline-flex", flexDirection: "column", lineHeight: 1.3 }}>
        <span style={{ fontSize: pxToRem(13), fontWeight: 600, color: "var(--text-paper)" }}>
          {t("kb.autoDigest.label")}
        </span>
        <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
          {t("kb.autoDigest.help")}
        </span>
      </span>
    </label>
  );
}
