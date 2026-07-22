/**
 * The `ask_user` question card (grill-me).
 *
 * The agent's question is an ordinary tool call whose `args` carry the
 * questions and their options; this turns it into something the user clicks
 * instead of a paragraph they have to answer by typing.
 *
 * Answering sends an ordinary message that records which question it answers.
 * Nothing waits for it — the turn already ended at this tool, and the next
 * turn picks the answer up from the transcript.
 *
 * The args have a FIXED shape — `{question, options: [{label, description}]}` —
 * guaranteed by the tool's strict schema (`ask_user_impl`, `agent/tools.py`).
 * That is deliberately the BACKEND's job: it names the fields to the model so the
 * model cannot send `option` instead of `label`. This card therefore EXPECTS the
 * correct shape rather than guessing around a loose one — papering over malformed
 * args here would just move the contract into the FE, where it drifts. The only
 * concession is returning `null` (not throwing) for genuinely broken args, since
 * a card that throws takes the whole transcript down.
 *
 * Layout: one option per row, each with its own supplement. A note about
 * "Postgres" is about Postgres, so it sits on that row rather than in one shared
 * box that could mean any option. Options are numbered, and a pick highlights
 * rather than sending on the click — with a note to type, firing on the first
 * click would send before the person finished.
 */
import { useState } from "react";

import type { ToolCallView } from "../pages/investigation/agentLog";

type Option = { label: string; description?: string };
type Question = { question: string; options: Option[] };

export type AskUserAnswer = { content: string; answers: string };

/** Read the questions out of the tool args. The shape is fixed and enforced by
 * the backend's strict schema, so this expects `{question, options:[{label,
 * description}]}` and returns `null` — render nothing, never throw — if it does
 * not get it. It does NOT guess alternative field names: that contract belongs
 * to the tool, not here. */
function parseQuestions(args: Record<string, unknown>): Question[] | null {
  const raw = args?.questions;
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const out: Question[] = [];
  for (const q of raw) {
    if (!q || typeof q !== "object") return null;
    const text = (q as { question?: unknown }).question;
    const options = (q as { options?: unknown }).options;
    if (typeof text !== "string" || !text.trim()) return null;
    if (!Array.isArray(options) || options.length === 0) return null;
    const parsed: Option[] = [];
    for (const o of options) {
      const label = (o as { label?: unknown })?.label;
      if (typeof label !== "string" || !label) return null;
      const description = (o as { description?: unknown })?.description;
      parsed.push({ label, description: typeof description === "string" ? description : "" });
    }
    out.push({ question: text, options: parsed });
  }
  return out;
}

const UNANSWERED = "(未選擇)";
// What "看不懂" reports. One button, never a menu: the user presses this while
// annoyed at having just read something useless, and asking them to classify
// their own irritation is a second insult. So it lists every cause and leaves
// the agent to work out which one it committed.
const DONT_UNDERSTAND =
  "看不懂 — 可能是繞了一大圈、術語太多、字很多但沒重點,或用了我沒看過的詞。" +
  "請重問同一題:直接講、用我熟悉的字、短一點,需要的話給個例子。";

/** How one question's answer reads in the transcript. The shapes stay
 * distinguishable on purpose: a rejection must not look like a choice, and a
 * note must not look like the answer itself. */
function answerLine(question: string, picked: string | undefined, note: string): string {
  const text = note.trim();
  if (picked === DONT_UNDERSTAND) return `${question} → ${DONT_UNDERSTAND}`;
  if (picked && text) return `${question} → ${picked}(補充:${text})`;
  if (picked) return `${question} → ${picked}`;
  if (text) return `${question} → 自訂:${text}`;
  return `${question} → ${UNANSWERED}`;
}

// Design-token styling so the card reads as part of the app rather than raw
// browser chrome. Inline objects (this file is inline-styled), but every colour
// comes from a declared token.
const optionRow = (active: boolean): React.CSSProperties => ({
  display: "flex",
  alignItems: "center",
  gap: 8,
  border: `1px solid ${active ? "var(--accent)" : "var(--paper-3)"}`,
  background: active ? "var(--accent-soft)" : "var(--paper)",
  borderRadius: 8,
  padding: 8,
});
const numBadge = (active: boolean): React.CSSProperties => ({
  flexShrink: 0,
  width: 22,
  height: 22,
  borderRadius: 999,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  fontSize: "0.78em",
  fontWeight: 600,
  color: active ? "var(--white)" : "var(--text-paper-d)",
  background: active ? "var(--accent)" : "var(--paper-2)",
});
const noteInput: React.CSSProperties = {
  flex: 1,
  minWidth: 80,
  padding: "4px 8px",
  border: "1px solid var(--paper-3)",
  borderRadius: 6,
  background: "var(--paper)",
  color: "var(--text-paper)",
  fontSize: "0.9em",
};
const plainBtn: React.CSSProperties = {
  padding: "5px 12px",
  border: "1px solid var(--paper-3)",
  background: "var(--paper-2)",
  color: "var(--text-paper)",
  borderRadius: 6,
  cursor: "pointer",
  flexShrink: 0,
  whiteSpace: "nowrap",
};

export function AskUserCard({
  call,
  onAnswer,
  answered,
}: {
  call: ToolCallView;
  onAnswer: (answer: AskUserAnswer) => void;
  /** Set once this question has been answered — the buttons are replaced by
   * the answer so it cannot be answered twice (two tabs, or a scroll back). */
  answered?: string;
}) {
  const questions = parseQuestions(call.args ?? {});
  // Chosen option per question; notes keyed by (question, label) so a note lives
  // with the specific option it supplements.
  const [picked, setPicked] = useState<Record<number, string>>({});
  const [optNote, setOptNote] = useState<Record<string, string>>({});
  const [freeText, setFreeText] = useState<Record<number, string>>({});

  if (!questions) return null;

  if (answered) {
    return (
      <div data-testid="ask-user-answered" style={{ opacity: 0.8 }}>
        {answered}
      </div>
    );
  }

  const noteKey = (qi: number, label: string) => `${qi} ${label}`;
  const send = () => {
    const lines = questions.map((q, i) => {
      const choice = picked[i];
      if (choice === DONT_UNDERSTAND) return answerLine(q.question, choice, "");
      const note = choice ? (optNote[noteKey(i, choice)] ?? "") : (freeText[i] ?? "");
      return answerLine(q.question, choice, note);
    });
    onAnswer({ content: lines.join("\n"), answers: call.call_id });
  };

  return (
    <div data-testid="ask-user" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {questions.map((q, i) => (
        <div key={i} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontWeight: 600 }}>{q.question}</div>

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {q.options.map((opt, oi) => {
                const active = picked[i] === opt.label;
                return (
                  <div key={opt.label} style={optionRow(active)}>
                    <button
                      type="button"
                      aria-pressed={active}
                      onClick={() => setPicked((p) => ({ ...p, [i]: opt.label }))}
                      title={opt.description || undefined}
                      style={{
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 8,
                        flex: "0 1 auto",
                        minWidth: 0,
                        background: "transparent",
                        border: "none",
                        padding: 0,
                        textAlign: "left",
                        cursor: "pointer",
                        color: "var(--text-paper)",
                      }}
                    >
                      <span style={numBadge(active)}>{oi + 1}</span>
                      <span
                        style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}
                      >
                        <span style={{ fontWeight: active ? 600 : 400 }}>{opt.label}</span>
                        {opt.description ? (
                          <span
                            style={{
                              opacity: 0.7,
                              fontSize: "0.85em",
                              color: "var(--text-paper-d)",
                            }}
                          >
                            {opt.description}
                          </span>
                        ) : null}
                      </span>
                    </button>
                    {/* This option's OWN supplement — a note about THIS choice,
                        not one shared box that could mean any of them. */}
                    <input
                      type="text"
                      aria-label={`補充:${opt.label}`}
                      value={optNote[noteKey(i, opt.label)] ?? ""}
                      placeholder="補充(選填)"
                      onChange={(e) =>
                        setOptNote((n) => ({ ...n, [noteKey(i, opt.label)]: e.target.value }))
                      }
                      onFocus={() => setPicked((p) => ({ ...p, [i]: opt.label }))}
                      style={noteInput}
                    />
                  </div>
                );
              })}
          </div>

          {/* Added by the card, never by the agent — so the way out exists even
              when a small model forgets to offer one. "看不懂" rejects the
              question; the input answers it in the user's own words. */}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              aria-pressed={picked[i] === DONT_UNDERSTAND}
              onClick={() => setPicked((p) => ({ ...p, [i]: DONT_UNDERSTAND }))}
              style={{
                ...plainBtn,
                borderColor: picked[i] === DONT_UNDERSTAND ? "var(--accent)" : "var(--paper-3)",
              }}
            >
              看不懂
            </button>
            <input
              type="text"
              aria-label="自己回答"
              value={freeText[i] ?? ""}
              placeholder="以上皆非,自己回答"
              onChange={(e) => {
                const v = e.target.value;
                setFreeText((n) => ({ ...n, [i]: v }));
                // A free answer clears any picked option — it IS the answer now,
                // so a leftover pick must not ride along with it.
                if (v) setPicked((p) => (p[i] ? { ...p, [i]: "" } : p));
              }}
              style={noteInput}
            />
          </div>
        </div>
      ))}

      <button
        type="button"
        onClick={send}
        style={{
          alignSelf: "flex-start",
          padding: "6px 16px",
          fontWeight: 600,
          border: "1px solid var(--accent)",
          background: "var(--accent)",
          color: "var(--white)",
          borderRadius: 6,
          cursor: "pointer",
        }}
      >
        送出
      </button>
    </div>
  );
}
