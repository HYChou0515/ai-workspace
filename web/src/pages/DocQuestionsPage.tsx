/**
 * The global "待釐清" inbox (#377) — every open clarification question the digest
 * raised while reading documents. Answer a term question and it becomes a
 * context card; answer a description question and it lands on the collection's
 * clarification wiki page. Discard drops a misclassified / irrelevant one. Reads
 * + writes go through the KB api; a `client` prop lets tests inject a fake.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { kbApi, type KbApi, type KbDocQuestion } from "../api/kb";
import { qk } from "../api/queryKeys";
import { Btn } from "../components/Btn";
import { Skeleton } from "../components/Skeleton";
import { useT } from "../lib/i18n";

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
    <div className="docq">
      <header className="docq__head">
        <h1 className="docq__title">{t("docq.title")}</h1>
        <p className="docq__sub">{t("docq.subtitle")}</p>
      </header>
      {isPending ? (
        <div data-testid="docq-loading">
          <Skeleton style={{ height: 96 }} />
        </div>
      ) : items.length === 0 ? (
        <div className="docq__empty">{t("docq.empty")}</div>
      ) : (
        <ul className="docq__list">
          {items.map((q) => (
            <QuestionRow
              key={q.id}
              q={q}
              onAnswer={(a) => answerMut.mutate({ id: q.id, answer: a })}
              onDiscard={() => discardMut.mutate(q.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function QuestionRow({
  q,
  onAnswer,
  onDiscard,
}: {
  q: KbDocQuestion;
  onAnswer: (answer: string) => void;
  onDiscard: () => void;
}) {
  const t = useT();
  const [answer, setAnswer] = useState("");
  const kindLabel = q.kind === "term" ? t("docq.kind.term") : t("docq.kind.description");
  return (
    <li className="docq__item">
      <div className="docq__meta">
        <span className="docq__kind" data-kind={q.kind}>
          {kindLabel}
        </span>
        {q.kind === "term" && q.term ? <span className="docq__term">{q.term}</span> : null}
        {q.kind === "term" && q.source_doc_ids.length > 0 ? (
          <span className="docq__sources">
            {t("docq.sources", { n: String(q.source_doc_ids.length) })}
          </span>
        ) : null}
      </div>
      <p className="docq__q">{q.question_text}</p>
      {q.kind === "description" && q.quote ? (
        <blockquote className="docq__quote">{q.quote}</blockquote>
      ) : null}
      <textarea
        className="docq__input"
        aria-label={t("docq.answer")}
        placeholder={t("docq.answerPlaceholder")}
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
      />
      <div className="docq__actions">
        <Btn variant="primary" size="sm" disabled={!answer.trim()} onClick={() => onAnswer(answer)}>
          {t("docq.answer")}
        </Btn>
        <Btn variant="ghost" size="sm" onClick={onDiscard}>
          {t("docq.discard")}
        </Btn>
      </div>
    </li>
  );
}
