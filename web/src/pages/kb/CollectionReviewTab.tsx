/**
 * The collection's 待審核 tab (#415) — right of Wiki. One inbox over two kinds of
 * pending items for THIS collection: finalized card-gen runs awaiting review
 * (the persistent home the picker sends you to after "自動生成") and the open
 * clarification questions the digest raised (#377, scoped here). Built on the
 * shared ReviewInbox shell — the same chrome the global "待釐清" page reuses.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { kbApi, type KbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { CardGenRunRow } from "./CardGenRunRow";
import { DocQuestionRow } from "./DocQuestionRow";
import { ReviewInbox } from "./ReviewInbox";

export function CollectionReviewTab({
  collectionId,
  client = kbApi,
}: {
  collectionId: string;
  client?: KbApi;
}) {
  const qc = useQueryClient();
  const runsQ = useQuery({
    queryKey: qk.kb.cardGenRuns(collectionId),
    queryFn: () => client.listCardGenRuns(collectionId),
  });
  const questionsQ = useQuery({
    queryKey: qk.kb.docQuestionsFor(collectionId),
    queryFn: () => client.getDocQuestions(collectionId),
  });

  const onRunResolved = () => {
    qc.invalidateQueries({ queryKey: qk.kb.cardGenRuns(collectionId) });
    // A committed run writes context cards — refresh the Cards view so the new
    // cards aren't hidden behind the query cache (dismiss just no-op refetches).
    qc.invalidateQueries({ queryKey: qk.kb.contextCards(collectionId) });
  };
  const invalidateQuestions = () =>
    qc.invalidateQueries({ queryKey: qk.kb.docQuestionsFor(collectionId) });
  const answerMut = useMutation({
    mutationFn: ({ id, answer }: { id: string; answer: string }) =>
      client.answerDocQuestion(id, answer),
    onSuccess: invalidateQuestions,
  });
  const discardMut = useMutation({
    mutationFn: (id: string) => client.discardDocQuestion(id),
    onSuccess: invalidateQuestions,
  });

  const runs = runsQ.data ?? [];
  const questions = questionsQ.data ?? [];

  return (
    <ReviewInbox
      title="待審核"
      subtitle="自動生成的卡片提案與待釐清的問題；審核後套用、回答或略過。"
      isLoading={runsQ.isPending || questionsQ.isPending}
      isEmpty={runs.length === 0 && questions.length === 0}
      emptyText="目前沒有待審核項目。"
    >
      {runs.map((run) => (
        <CardGenRunRow key={run.run_id} run={run} client={client} onResolved={onRunResolved} />
      ))}
      {questions.map((q) => (
        <DocQuestionRow
          key={q.id}
          q={q}
          onAnswer={(a) => answerMut.mutate({ id: q.id, answer: a })}
          onDiscard={() => discardMut.mutate(q.id)}
        />
      ))}
    </ReviewInbox>
  );
}
