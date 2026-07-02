/**
 * WikiCorrectionDialog (#397) — the "回報有誤" flow. Opens blank; a one-click
 * "AI 幫我草擬" drafts the correction from the flagged Q&A (adaptive: it drafts
 * if it can tell what's wrong, else asks 1–3 short questions — Q12). The user
 * reviews/edits the draft and submits; the correction lands on the immune
 * corrections page and the corrector agent applies it to the wiki.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { KbApi, WikiCorrectionQA } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { useT } from "../../lib/i18n";

export function WikiCorrectionDialog({
  collectionId,
  question,
  answer,
  wikiPages = [],
  client,
  onClose,
}: {
  collectionId: string;
  question: string;
  answer: string;
  wikiPages?: string[];
  client: KbApi;
  onClose: () => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [instruction, setInstruction] = useState("");
  const [targetPage, setTargetPage] = useState("");
  // #397 Q12: accumulated prior mini-grill answers + the current unanswered round.
  const [answered, setAnswered] = useState<WikiCorrectionQA[]>([]);
  const [pending, setPending] = useState<string[]>([]);
  const [pendingAnswers, setPendingAnswers] = useState<string[]>([]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const draftMut = useMutation({
    mutationFn: () => {
      const round = pending.map((q, i) => ({ question: q, answer: pendingAnswers[i] ?? "" }));
      const nextAnswered = [...answered, ...round];
      return client
        .draftWikiCorrection(collectionId, {
          question,
          answer,
          wiki_pages: wikiPages,
          answered: nextAnswered,
        })
        .then((res) => ({ res, nextAnswered }));
    },
    onSuccess: ({ res, nextAnswered }) => {
      setAnswered(nextAnswered);
      if (res.action === "ask" && res.questions.length > 0) {
        setPending(res.questions);
        setPendingAnswers(res.questions.map(() => ""));
      } else {
        setPending([]);
        setInstruction(res.instruction);
        setTargetPage(res.target_page);
      }
    },
  });

  const submitMut = useMutation({
    mutationFn: () =>
      client.submitWikiCorrection(collectionId, {
        instruction: instruction.trim(),
        target_page: targetPage.trim() || undefined,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.kb.wikiStatus(collectionId) });
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!instruction.trim() || submitMut.isPending) return;
    submitMut.mutate();
  };

  if (submitMut.isSuccess) {
    return (
      <div className="kb-modal" role="presentation" onClick={onClose}>
        <div
          className="kb-modal__card"
          role="dialog"
          aria-modal
          aria-label={t("wikiCorrection.title")}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="kb-modal__body">
            <p>{t("wikiCorrection.done")}</p>
          </div>
          <footer className="kb-modal__foot">
            <button type="button" className="kb-btn kb-btn--primary" onClick={onClose}>
              {t("wikiCorrection.cancel")}
            </button>
          </footer>
        </div>
      </div>
    );
  }

  return (
    <div className="kb-modal" role="presentation" onClick={onClose}>
      <form
        className="kb-modal__card"
        role="dialog"
        aria-modal
        aria-label={t("wikiCorrection.title")}
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <header className="kb-modal__head">
          <div className="caps">Wiki</div>
          <h2 className="kb-modal__title">{t("wikiCorrection.title")}</h2>
        </header>

        <div className="kb-modal__body">
          <p className="kb-field__hint">{t("wikiCorrection.intro")}</p>

          <button
            type="button"
            className="kb-btn"
            disabled={draftMut.isPending}
            onClick={() => draftMut.mutate()}
          >
            <Icon name="sparkle" size={13} />{" "}
            {draftMut.isPending ? t("wikiCorrection.generating") : t("wikiCorrection.generate")}
          </button>

          {pending.length > 0 && (
            <div className="kb-field">
              <span className="kb-field__label">{t("wikiCorrection.questionsIntro")}</span>
              {pending.map((q, i) => (
                <label className="kb-field" key={i}>
                  <span className="kb-field__label">{q}</span>
                  <input
                    className="kb-input"
                    value={pendingAnswers[i] ?? ""}
                    onChange={(e) =>
                      setPendingAnswers((prev) => {
                        const next = [...prev];
                        next[i] = e.target.value;
                        return next;
                      })
                    }
                  />
                </label>
              ))}
            </div>
          )}

          <label className="kb-field">
            <span className="kb-field__label">{t("wikiCorrection.instructionLabel")}</span>
            <textarea
              className="kb-input kb-textarea"
              rows={4}
              placeholder={t("wikiCorrection.instructionPlaceholder")}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
            />
          </label>

          <label className="kb-field">
            <span className="kb-field__label">{t("wikiCorrection.targetLabel")}</span>
            <input
              className="kb-input"
              placeholder={t("wikiCorrection.targetPlaceholder")}
              value={targetPage}
              onChange={(e) => setTargetPage(e.target.value)}
            />
          </label>

          {submitMut.isError && <p className="kb-drawer__error">{t("wikiCorrection.error")}</p>}
        </div>

        <footer className="kb-modal__foot">
          <button type="button" className="kb-btn" onClick={onClose}>
            {t("wikiCorrection.cancel")}
          </button>
          <button
            type="submit"
            className="kb-btn kb-btn--primary"
            disabled={!instruction.trim() || submitMut.isPending}
          >
            <Icon name="check" size={13} />{" "}
            {submitMut.isPending ? t("wikiCorrection.submitting") : t("wikiCorrection.submit")}
          </button>
        </footer>
      </form>
    </div>
  );
}
