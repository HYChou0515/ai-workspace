/**
 * The collection's 待審核 tab (#415) — right of Wiki. Lists this collection's
 * finalized card-gen runs awaiting review (the persistent home the picker sends
 * you to after "自動生成", instead of a blocking modal). Built on the shared
 * ReviewInbox shell so the future cross-collection overview reuses the chrome.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { kbApi, type KbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { CardGenRunRow } from "./CardGenRunRow";
import { ReviewInbox } from "./ReviewInbox";

export function CollectionReviewTab({
  collectionId,
  client = kbApi,
}: {
  collectionId: string;
  client?: KbApi;
}) {
  const qc = useQueryClient();
  const { data: runs, isPending } = useQuery({
    queryKey: qk.kb.cardGenRuns(collectionId),
    queryFn: () => client.listCardGenRuns(collectionId),
  });
  const onResolved = () => qc.invalidateQueries({ queryKey: qk.kb.cardGenRuns(collectionId) });

  return (
    <ReviewInbox
      title="待審核"
      subtitle="自動生成的卡片提案，展開後可編輯、套用或略過。"
      isLoading={isPending}
      isEmpty={(runs ?? []).length === 0}
      emptyText="目前沒有待審核項目。"
    >
      {(runs ?? []).map((run) => (
        <CardGenRunRow key={run.run_id} run={run} client={client} onResolved={onResolved} />
      ))}
    </ReviewInbox>
  );
}
