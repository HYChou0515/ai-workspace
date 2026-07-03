/**
 * The global "待釐清" inbox (#377) — every open clarification question the digest
 * raised while reading documents, across all collections. Answer a term question
 * and it becomes a context card; answer a description question and it lands on
 * the collection's clarification wiki page; discard drops a misclassified one.
 *
 * #415: this is the global-scope instance of the shared ReviewInbox shell — the
 * same chrome the per-collection 待審核 tab uses (with a `collection_id` scope),
 * rendering the shared DocQuestionRow rows.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { kbApi, type KbApi } from "../api/kb";
import { qk } from "../api/queryKeys";
import { useT } from "../lib/i18n";
import { DocQuestionRow } from "./kb/DocQuestionRow";
import { ReviewInbox } from "./kb/ReviewInbox";

export function DocQuestionsPage({ client = kbApi }: { client?: KbApi }) {
  const t = useT();
  const qc = useQueryClient();
  const { data: items = [], isPending } = useQuery({
    queryKey: qk.kb.docQuestions,
    queryFn: () => client.getDocQuestions(),
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: qk.kb.docQuestions });
  const answerMut = useMutation({
    mutationFn: ({ id, answer }: { id: string; answer: string }) =>
      client.answerDocQuestion(id, answer),
    onSuccess: invalidate,
  });
  const discardMut = useMutation({
    mutationFn: (id: string) => client.discardDocQuestion(id),
    onSuccess: invalidate,
  });

  return (
    <ReviewInbox
      title={t("docq.title")}
      subtitle={t("docq.subtitle")}
      isLoading={isPending}
      isEmpty={items.length === 0}
      emptyText={t("docq.empty")}
    >
      {items.map((q) => (
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
