import { useMutation, useQueryClient } from "@tanstack/react-query";

import { kbApi, type KbApi, type KbCollection } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { useT } from "../../lib/i18n";
import { SettingRow } from "./SettingRow";

/**
 * "Auto-generate cards" row on the collection settings panel (#377). When on,
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
    mutationFn: (next: boolean) =>
      client.updateCollection(collection.resource_id, { auto_digest: next }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
  });

  return (
    <SettingRow
      icon="sparkle"
      title={t("kb.autoDigest.label")}
      desc={t("kb.autoDigest.help")}
      on={collection.auto_digest}
      disabled={mut.isPending}
      onToggle={() => mut.mutate(!collection.auto_digest)}
      testId="kb-autodigest-toggle"
    />
  );
}
