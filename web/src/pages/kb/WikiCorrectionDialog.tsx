/**
 * WikiCorrectionDialog (#397) — the "回報有誤" flow. Opens blank; a one-click
 * "AI 幫我草擬" drafts the correction from the flagged Q&A (adaptive: it drafts
 * if it can tell what's wrong, else asks 1–3 short questions — Q12). The user
 * reviews/edits the draft and submits; the correction lands on the immune
 * corrections page and the corrector agent applies it to the wiki.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import type { KbApi, WikiCorrectionQA } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { ModalShell } from "../../components/ModalShell";
import { useDirtyClose } from "../../hooks/useDirtyClose";
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

  // #779: the instruction is hand-written, the AI draft cost a model call, and
  // the mini-grill answers are a conversation — none of it is stored until submit.
  const dirty =
    instruction.trim() !== "" ||
    targetPage.trim() !== "" ||
    answered.length > 0 ||
    pendingAnswers.some((a) => a.trim() !== "");
  const attemptClose = useDirtyClose(dirty, onClose);

  // Escape was a hand-rolled listener here. ModalShell owns it now (#779 P5),
  // together with the focus trap and focus restore this overlay never had.

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
    // Renders its own error in the drawer (`submitMut.isError` below).
    meta: { silentError: true },
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
      <ModalShell
        onClose={onClose}
        // The correction is filed; there is nothing left to lose, so this one
        // keeps a live backdrop.
        closeOnBackdrop
        ariaLabel={t("wikiCorrection.title")}
        panelClassName="kb-modal__card"
        width="min(520px, 100%)"
        maxWidth="100%"
        panelStyle={{
          padding: 0,
          background: "var(--paper)",
          boxShadow: "0 20px 60px rgba(20, 22, 28, 0.3)",
        }}
        backdropStyle={{
          background: "rgba(20, 22, 28, 0.55)",
          backdropFilter: "blur(4px)",
          padding: 16,
        }}
      >
        <div className="kb-modal__body">
          <p>{t("wikiCorrection.done")}</p>
        </div>
        <footer className="kb-modal__foot">
          {/* dirty-close-exempt: the correction is filed — this branch renders
              only after a successful submit, so there is nothing left to lose. */}
          <button type="button" className="kb-btn kb-btn--primary" onClick={onClose}>
            {t("wikiCorrection.cancel")}
          </button>
        </footer>
      </ModalShell>
    );
  }

  // #779: the backdrop is the accidental exit — while there is something written
  // it does nothing rather than raising a prompt about an unintended click.
  return (
    <ModalShell
      onClose={attemptClose}
      ariaLabel={t("wikiCorrection.title")}
      panelClassName="kb-modal__card"
      width="min(520px, 100%)"
      maxWidth="100%"
      panelStyle={{
        padding: 0,
        background: "var(--paper)",
        boxShadow: "0 20px 60px rgba(20, 22, 28, 0.3)",
      }}
      backdropStyle={{
        background: "rgba(20, 22, 28, 0.55)",
        backdropFilter: "blur(4px)",
        padding: 16,
      }}
    >
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
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
          <button type="button" className="kb-btn" onClick={attemptClose}>
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
    </ModalShell>
  );
}
