/**
 * One clarification-question row (#377), extracted for #415 so the SAME row
 * renders in the global "待釐清" inbox and in a collection's 待審核 tab. Answering a
 * term question mints a context card; a description answer lands on the wiki
 * clarification page; discard drops it. The row owns only its draft answer — the
 * container wires answer / discard.
 */
import { useState } from "react";

import type { KbDocQuestion } from "../../api/kb";
import { Btn } from "../../components/Btn";
import { useT } from "../../lib/i18n";

export function DocQuestionRow({
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
