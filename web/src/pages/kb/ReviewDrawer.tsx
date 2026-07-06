/**
 * ReviewDrawer (#481) — the right-anchored master-detail panel for one review
 * item. A card proposal is editable (title/body) with its evidence + accept/reject
 * + save; a clarification question gets an answer box + submit/discard. When the
 * user lacks write access on the collection the whole panel is read-only with a
 * hint. Esc + backdrop close.
 */
import { useEffect, useState } from "react";

import type { KbProposedCard, KbReviewCard, KbReviewQuestion } from "../../api/kb";
import { Btn } from "../../components/Btn";
import { Icon } from "../../components/Icon";
import type { useReviewInbox } from "../../hooks/useReviewInbox";
import { useT } from "../../lib/i18n";

type Actions = ReturnType<typeof useReviewInbox>;
export type ReviewItem =
  | { kind: "card"; data: KbReviewCard }
  | { kind: "question"; data: KbReviewQuestion };

export function ReviewDrawer({
  item,
  resolved,
  actions,
  onClose,
}: {
  item: ReviewItem;
  resolved: boolean;
  actions: Actions;
  onClose: () => void;
}) {
  const t = useT();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const canAct = item.data.can_act && !resolved;

  return (
    <div className="rvw-drawer__backdrop" onClick={onClose}>
      <aside
        className="rvw-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={item.kind === "card" ? t("review.type.card") : t("review.type.question")}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="rvw-drawer__head">
          <span className="rvw-drawer__coll">{item.data.collection_name}</span>
          <button
            type="button"
            className="rvw-drawer__close"
            aria-label={t("review.drawer.close")}
            onClick={onClose}
          >
            <Icon name="x" size={16} />
          </button>
        </header>
        {!canAct && <div className="rvw-drawer__readonly">{t("review.readonly.hint")}</div>}
        {item.kind === "card" ? (
          <CardDetail row={item.data} canAct={canAct} actions={actions} onClose={onClose} />
        ) : (
          <QuestionDetail row={item.data} canAct={canAct} actions={actions} onClose={onClose} />
        )}
      </aside>
    </div>
  );
}

function CardDetail({
  row,
  canAct,
  actions,
  onClose,
}: {
  row: KbReviewCard;
  canAct: boolean;
  actions: Actions;
  onClose: () => void;
}) {
  const t = useT();
  const c = row.card;
  const [title, setTitle] = useState(c.title);
  const [body, setBody] = useState(c.body);
  const edited = title !== c.title || body !== c.body;

  const decide = (decision: "accepted" | "rejected") =>
    actions.decide.mutate({
      runId: row.run_id,
      cardId: c.id,
      decision: c.decision === decision ? "pending" : decision,
    });
  const save = () => {
    const next: KbProposedCard = { ...c, title, body };
    actions.update.mutate({ runId: row.run_id, card: next });
  };

  return (
    <div className="rvw-drawer__body">
      <div className="rvw-drawer__badges">
        <span className="rvw__mode" data-mode={c.mode}>
          {c.mode === "update" ? t("review.mode.update") : t("review.mode.new")}
        </span>
        {!c.confident && <span className="rvw__warn">⚠️ {t("review.uncertain")}</span>}
      </div>

      <label className="rvw-drawer__field">
        <span>{t("review.drawer.title")}</span>
        <input
          value={title}
          disabled={!canAct}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label className="rvw-drawer__field">
        <span>{t("review.drawer.body")}</span>
        <textarea rows={6} value={body} disabled={!canAct} onChange={(e) => setBody(e.target.value)} />
      </label>

      <div className="rvw-drawer__keys">
        <span className="rvw-drawer__label">{t("review.drawer.keys")}</span>
        {c.keys.map((k) => (
          <code key={k} className="rvw__key">
            {k}
          </code>
        ))}
      </div>

      {c.mode === "update" && (
        <p className="rvw-drawer__overwrite">
          <Icon name="undo" size={13} /> {t("review.drawer.overwrites")}
        </p>
      )}

      {c.provenance.length > 0 && (
        <div className="rvw-drawer__prov">
          <span className="rvw-drawer__label">{t("review.drawer.provenance")}</span>
          <ul>
            {c.provenance.map((p, i) => (
              <li key={`${p.path}-${i}`}>
                <code className="rvw-drawer__path">{p.path}</code>
                {p.snippet && <blockquote>{p.snippet}</blockquote>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {canAct && (
        <footer className="rvw-drawer__foot">
          <Btn
            variant={c.decision === "accepted" ? "primary" : "secondary"}
            size="sm"
            active={c.decision === "accepted"}
            onClick={() => decide("accepted")}
          >
            {t("review.action.accept")}
          </Btn>
          <Btn
            variant={c.decision === "rejected" ? "danger" : "ghost"}
            size="sm"
            active={c.decision === "rejected"}
            onClick={() => decide("rejected")}
          >
            {t("review.action.reject")}
          </Btn>
          <span style={{ flex: 1 }} />
          <Btn variant="ghost" size="sm" disabled={!edited} onClick={save}>
            {t("review.drawer.save")}
          </Btn>
        </footer>
      )}
      {!canAct && (
        <footer className="rvw-drawer__foot">
          <Btn variant="ghost" size="sm" onClick={onClose}>
            {t("review.drawer.close")}
          </Btn>
        </footer>
      )}
    </div>
  );
}

function QuestionDetail({
  row,
  canAct,
  actions,
  onClose,
}: {
  row: KbReviewQuestion;
  canAct: boolean;
  actions: Actions;
  onClose: () => void;
}) {
  const t = useT();
  const q = row.question;
  const [answer, setAnswer] = useState("");

  const submit = () => {
    actions.answer.mutate({ id: q.id, answer });
    onClose();
  };
  const discard = () => {
    actions.discard.mutate(q.id);
    onClose();
  };

  return (
    <div className="rvw-drawer__body">
      <div className="rvw-drawer__badges">
        <span className="rvw__kind" data-kind={q.kind}>
          {q.kind === "term" ? t("docq.kind.term") : t("docq.kind.description")}
        </span>
        {q.term && <span className="rvw-drawer__term">{q.term}</span>}
      </div>
      <p className="rvw-drawer__q">{q.question_text}</p>
      {q.kind === "description" && q.quote && (
        <blockquote className="rvw-drawer__quote">{q.quote}</blockquote>
      )}
      {q.kind === "term" && q.source_doc_ids.length > 0 && (
        <p className="rvw-drawer__sources">
          {t("docq.sources", { n: q.source_doc_ids.length })}
        </p>
      )}
      {canAct ? (
        <>
          <textarea
            className="rvw-drawer__answer"
            aria-label={t("docq.answer")}
            placeholder={t("docq.answerPlaceholder")}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            rows={5}
          />
          <footer className="rvw-drawer__foot">
            <Btn variant="primary" size="sm" disabled={!answer.trim()} onClick={submit}>
              {t("docq.answer")}
            </Btn>
            <Btn variant="ghost" size="sm" onClick={discard}>
              {t("docq.discard")}
            </Btn>
          </footer>
        </>
      ) : (
        <footer className="rvw-drawer__foot">
          <Btn variant="ghost" size="sm" onClick={onClose}>
            {t("review.drawer.close")}
          </Btn>
        </footer>
      )}
    </div>
  );
}
