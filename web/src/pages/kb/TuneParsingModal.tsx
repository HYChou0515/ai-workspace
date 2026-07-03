// #356: the "Tune parsing" modal — an interactive prompt-tuning playground for
// ONE document. Type a representative question, see how deep this doc's content
// ranks (deeper than a user sees), edit the parse prompt, re-parse to compare,
// and "Try answer" the question from the doc's top-k passages (the kb_chat model,
// fixed context). Save the prompt for THIS document (the escape hatch) or for the
// whole collection. Nothing is written until you apply; applying takes effect on
// the next re-index.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { kbApi, type KbApi, type KbProbeResult, type KbProbeSide } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { useT } from "../../lib/i18n";
import { pxToRem } from "../../lib/pxToRem";

// Exponential slider: raw position s∈[0,100] → k∈[1,100], midpoint s=50 ⇒ k≈10.
function sliderToK(s: number): number {
  return Math.max(1, Math.min(100, Math.round(100 ** (s / 100))));
}
function kToSlider(k: number): number {
  return Math.round((Math.log(k) / Math.log(100)) * 100);
}

type AnswerState = { text: string; status: "streaming" | "done" | "error"; error?: string };

export function TuneParsingModal({
  collectionId,
  docId,
  docPath,
  docGuidance,
  onClose,
  client = kbApi,
}: {
  collectionId: string;
  docId: string;
  docPath: string;
  /** #356: the doc's current per-doc override; "" / undefined ⇒ inherits the collection. */
  docGuidance?: string;
  onClose: () => void;
  client?: KbApi;
}) {
  const t = useT();
  const qc = useQueryClient();
  const collectionsQ = useQuery({
    queryKey: qk.kb.collections,
    queryFn: () => client.listCollections(),
  });
  const collectionGuidance =
    collectionsQ.data?.find((c) => c.resource_id === collectionId)?.parser_guidance ?? "";
  const hasOverride = (docGuidance ?? "").trim().length > 0;
  // The doc's EFFECTIVE guidance prefills the editor: its own override if set,
  // else the collection's. (#356 REPLACE semantics — what's in the box is exactly
  // what this doc would get.)
  const effectiveGuidance = hasOverride ? (docGuidance as string) : collectionGuidance;

  const [question, setQuestion] = useState("");
  const [guidance, setGuidance] = useState(effectiveGuidance);
  // Re-sync the editor once the collection's guidance loads (only matters when the
  // doc inherits — an override is known synchronously from props).
  useEffect(() => setGuidance(effectiveGuidance), [effectiveGuidance]);
  const [sliderPos, setSliderPos] = useState(kToSlider(5)); // default k=5 (the real top_k)
  const k = sliderToK(sliderPos);
  const [result, setResult] = useState<KbProbeResult | null>(null);
  const [beforeOpen, setBeforeOpen] = useState(false); // #356: Before collapsed by default
  const [answers, setAnswers] = useState<{ before?: AnswerState; after?: AnswerState }>({});

  const probeMut = useMutation({
    mutationFn: (withGuidance: boolean) =>
      client.probeFindability({
        doc_id: docId,
        question,
        guidance: withGuidance ? guidance : null,
        k,
      }),
    onSuccess: (r) => {
      setResult(r);
      setAnswers({}); // a fresh probe invalidates the previous answers
    },
  });

  const invalidateDoc = () => {
    void qc.invalidateQueries({ queryKey: qk.kb.collections });
    void qc.invalidateQueries({ queryKey: qk.kb.documents(collectionId) });
    // #395: the override now arrives via the doc's render — refresh it too so
    // reopening Tune shows what was just saved.
    void qc.invalidateQueries({ queryKey: qk.kb.doc(docId) });
  };
  const saveDocMut = useMutation({
    mutationFn: () => client.setDocumentGuidance(docId, guidance),
    onSuccess: invalidateDoc,
  });
  const clearDocMut = useMutation({
    mutationFn: () => client.setDocumentGuidance(docId, ""),
    onSuccess: invalidateDoc,
  });
  const applyCollMut = useMutation({
    mutationFn: () => client.updateCollection(collectionId, { parser_guidance: guidance }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
  });
  const reindexMut = useMutation({
    mutationFn: () => client.reindexDocument(docId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.documents(collectionId) }),
  });

  async function runAnswer(which: "before" | "after") {
    setAnswers((a) => ({ ...a, [which]: { text: "", status: "streaming" } }));
    try {
      for await (const ev of client.answerFindability({
        doc_id: docId,
        question,
        k,
        // Before answers from the indexed doc; After from the candidate re-parse.
        guidance: which === "after" ? guidance : undefined,
      })) {
        if (ev.type === "message_delta" && !ev.reasoning) {
          const text = ev.text;
          setAnswers((a) => ({
            ...a,
            [which]: { ...(a[which] ?? { text: "", status: "streaming" }), text: (a[which]?.text ?? "") + text },
          }));
        } else if (ev.type === "error") {
          setAnswers((a) => ({
            ...a,
            [which]: { text: a[which]?.text ?? "", status: "error", error: ev.message },
          }));
        } else if (ev.type === "done") {
          setAnswers((a) => ({
            ...a,
            [which]: { ...(a[which] ?? { text: "", status: "streaming" }), status: "done" },
          }));
        }
      }
    } catch (e) {
      setAnswers((a) => ({
        ...a,
        [which]: { text: a[which]?.text ?? "", status: "error", error: String(e) },
      }));
    }
  }

  const canProbe = question.trim().length > 0 && !probeMut.isPending;
  const saved = saveDocMut.isSuccess || clearDocMut.isSuccess || applyCollMut.isSuccess;

  return (
    <div
      role="presentation"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 200,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${t("kb.tuneParsing.title")} — ${docPath}`}
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 640,
          maxWidth: "94vw",
          maxHeight: "88vh",
          overflow: "auto",
          background: "var(--white)",
          borderRadius: "var(--radius-card)",
          border: "1px solid var(--paper-3)",
          boxShadow: "0 16px 40px rgba(0,0,0,0.22)",
          padding: 20,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <strong style={{ fontSize: pxToRem(14) }}>{t("kb.tuneParsing.title")}</strong>
          <span className="mono" style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
            {docPath}
          </span>
          <span style={{ flex: 1 }} />
          <button type="button" className="kb-btn" aria-label={t("kb.tuneParsing.close")} onClick={onClose}>
            {t("kb.tuneParsing.close")}
          </button>
        </div>

        <p style={{ fontSize: pxToRem(11.5), color: "var(--text-paper-d)", margin: 0, lineHeight: 1.45 }}>
          {t("kb.tuneParsing.description")}
        </p>

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="caps" style={{ fontSize: pxToRem(11) }}>
            {t("kb.tuneParsing.question")}
          </span>
          <input
            aria-label={t("kb.tuneParsing.question")}
            value={question}
            placeholder={t("kb.tuneParsing.questionPlaceholder")}
            onChange={(e) => setQuestion(e.target.value)}
            style={inputStyle}
          />
        </label>

        <label style={{ display: "flex", alignItems: "center", gap: 10 }} title={t("kb.tuneParsing.kTitle")}>
          <span className="caps" style={{ fontSize: pxToRem(11), whiteSpace: "nowrap" }}>
            {t("kb.tuneParsing.k", { k })}
          </span>
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={sliderPos}
            aria-label={t("kb.tuneParsing.k", { k })}
            onChange={(e) => setSliderPos(Number(e.target.value))}
            style={{ flex: 1 }}
          />
        </label>

        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="kb-btn" disabled={!canProbe} onClick={() => probeMut.mutate(false)}>
            {t("kb.tuneParsing.checkRanks")}
          </button>
          <button
            type="button"
            className="kb-btn kb-btn--primary"
            disabled={!canProbe}
            onClick={() => probeMut.mutate(true)}
            title={t("kb.tuneParsing.reparseTitle")}
          >
            {t("kb.tuneParsing.reparse")}
          </button>
          {probeMut.isPending && (
            <span style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)", alignSelf: "center" }}>
              {t("kb.tuneParsing.running")}
            </span>
          )}
          {probeMut.isError && (
            <span role="alert" style={{ fontSize: pxToRem(12), color: "var(--warn)", alignSelf: "center" }}>
              {t("kb.tuneParsing.probeFailed")}
            </span>
          )}
        </div>

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="caps" style={{ fontSize: pxToRem(11) }}>
            {t("kb.tuneParsing.guidance")}
          </span>
          <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
            {hasOverride ? t("kb.tuneParsing.sourceCustom") : t("kb.tuneParsing.sourceInherited")}
          </span>
          <textarea
            aria-label={t("kb.tuneParsing.guidance")}
            value={guidance}
            placeholder={t("kb.tuneParsing.guidancePlaceholder")}
            onChange={(e) => setGuidance(e.target.value)}
            style={{ ...inputStyle, minHeight: 88, resize: "vertical" }}
          />
        </label>

        {result && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <ProbeBox
              which="before"
              title={t("kb.tuneParsing.before")}
              side={result.before}
              k={result.top_k}
              open={beforeOpen}
              onToggle={() => setBeforeOpen((v) => !v)}
              answer={answers.before}
              onAnswer={() => void runAnswer("before")}
            />
            {result.after && (
              <ProbeBox
                which="after"
                title={t("kb.tuneParsing.after")}
                side={result.after}
                k={result.top_k}
                open
                answer={answers.after}
                onAnswer={() => void runAnswer("after")}
              />
            )}
          </div>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
          <button
            type="button"
            className="kb-btn kb-btn--primary"
            disabled={saveDocMut.isPending}
            onClick={() => saveDocMut.mutate()}
          >
            {t("kb.tuneParsing.saveDoc")}
          </button>
          <button
            type="button"
            className="kb-btn"
            disabled={applyCollMut.isPending}
            onClick={() => {
              if (window.confirm(t("kb.tuneParsing.applyConfirm"))) applyCollMut.mutate();
            }}
          >
            {t("kb.tuneParsing.applyCollection")}
          </button>
          {hasOverride && (
            <button
              type="button"
              className="kb-btn"
              disabled={clearDocMut.isPending}
              onClick={() => clearDocMut.mutate()}
            >
              {t("kb.tuneParsing.clearOverride")}
            </button>
          )}
        </div>

        {saved && (
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
              {t("kb.tuneParsing.savedNudge")}
            </span>
            <button
              type="button"
              className="kb-btn"
              disabled={reindexMut.isPending}
              onClick={() => reindexMut.mutate()}
            >
              {t("kb.tuneParsing.reindexDoc")}
            </button>
            {reindexMut.isSuccess && (
              <span style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
                {t("kb.doc.processing")}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ProbeBox({
  which,
  title,
  side,
  k,
  open,
  onToggle,
  answer,
  onAnswer,
}: {
  which: "before" | "after";
  title: string;
  side: KbProbeSide;
  k: number;
  open: boolean;
  onToggle?: () => void;
  answer?: AnswerState;
  onAnswer: () => void;
}) {
  const t = useT();
  const rankLabel =
    side.best_rank == null ? t("kb.tuneParsing.notInTop", { k }) : t("kb.tuneParsing.bestRank", { rank: side.best_rank });
  return (
    <section
      aria-label={title}
      style={{
        border: "1px solid var(--paper-3)",
        borderRadius: 8,
        padding: 12,
        background: "var(--paper-2)",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        {onToggle ? (
          <button
            type="button"
            className="kb-btn"
            aria-expanded={open}
            onClick={onToggle}
            style={{ padding: "0 6px" }}
          >
            {open ? "▾" : "▸"} <span className="caps" style={{ fontSize: pxToRem(11) }}>{title}</span>
          </button>
        ) : (
          <span className="caps" style={{ fontSize: pxToRem(11) }}>
            {title}
          </span>
        )}
        <span style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>{rankLabel}</span>
      </div>

      {open && (
        <div style={{ marginTop: 8 }}>
          {side.passages.length === 0 ? (
            <p style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)", margin: 0 }}>
              {t("kb.tuneParsing.emptyForQuestion", { k })}
            </p>
          ) : (
            <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
              {side.passages.map((p) => (
                <li
                  key={`${p.rank}-${p.location}`}
                  style={{ display: "flex", gap: 8, alignItems: "baseline", fontSize: pxToRem(12) }}
                >
                  <span
                    className="mono"
                    style={{ fontWeight: 600, color: p.in_top_k ? "var(--accent-h)" : "var(--text-paper-d)" }}
                  >
                    #{p.rank}
                  </span>
                  {p.location && <span style={{ color: "var(--text-paper-d)" }}>{p.location}</span>}
                  <span
                    style={{
                      color: "var(--text-paper-d)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {p.text}
                  </span>
                </li>
              ))}
            </ul>
          )}

          <div style={{ marginTop: 10 }}>
            <button
              type="button"
              className="kb-btn"
              aria-label={`${t("kb.tuneParsing.tryAnswer")} (${title})`}
              disabled={answer?.status === "streaming"}
              onClick={onAnswer}
            >
              {t("kb.tuneParsing.tryAnswer")}
            </button>
            {answer && (
              <div
                aria-label={`${which} answer`}
                style={{
                  marginTop: 8,
                  fontSize: pxToRem(12),
                  color: "var(--text-paper)",
                  whiteSpace: "pre-wrap",
                  lineHeight: 1.5,
                }}
              >
                {answer.status === "error" ? (
                  <span role="alert" style={{ color: "var(--warn)" }}>
                    {t("kb.tuneParsing.answerFailed")}: {answer.error}
                  </span>
                ) : (
                  answer.text
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid var(--paper-3)",
  background: "var(--paper)",
  font: "inherit",
  fontSize: pxToRem(13),
  lineHeight: 1.5,
  boxSizing: "border-box",
};
