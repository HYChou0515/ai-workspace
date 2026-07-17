import { useMutation, useQueryClient } from "@tanstack/react-query";

import { kbApi, type KbApi, type KbCollection } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { useIsSuperuser } from "../../hooks/useIsSuperuser";
import { useT } from "../../lib/i18n";
import { pxToRem } from "../../lib/pxToRem";

/**
 * Superuser-only "Global" toggle on the collection page. Flagging a collection
 * global adds it to every AI conversation's baseline retrieval scope (the BE
 * enforces the `PUT …/global` permission; a non-superuser never sees the
 * control). Reflects `collection.is_global`; a change flips it via
 * `setCollectionGlobal` and refreshes the collections list.
 */
export function GlobalToggle({
  collection,
  client = kbApi,
}: {
  collection: KbCollection;
  client?: KbApi;
}) {
  const t = useT();
  const qc = useQueryClient();
  const isSuperuser = useIsSuperuser();
  const mut = useMutation({
    mutationFn: (next: boolean) => client.setCollectionGlobal(collection.resource_id, next),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
  });

  // Not a superuser → the control is invisible (the BE would 403 anyway).
  if (!isSuperuser) return null;

  return (
    <label
      className="kb-global-toggle"
      style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}
    >
      <input
        type="checkbox"
        data-testid="kb-global-toggle"
        checked={collection.is_global}
        disabled={mut.isPending}
        onChange={(e) => mut.mutate(e.target.checked)}
      />
      <span style={{ display: "inline-flex", flexDirection: "column", lineHeight: 1.3 }}>
        <span style={{ fontSize: pxToRem(13), fontWeight: 600, color: "var(--text-paper)" }}>
          {t("kb.global.label")}
        </span>
        <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
          {t("kb.global.help")}
        </span>
      </span>
    </label>
  );
}
