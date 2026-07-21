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
 */
import { useState } from "react";

import type { ToolCallView } from "../pages/investigation/agentLog";

type Option = { label: string; description?: string };
type Question = { question: string; options: Option[] };

export type AskUserAnswer = { content: string; answers: string };

/** Read the questions out of model-produced args. Anything malformed yields
 * `null` so the card can render nothing — a card that throws would take the
 * whole transcript down with it. */
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

/** How one question's answer reads in the transcript. The four shapes stay
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
  const [picked, setPicked] = useState<Record<number, string>>({});
  const [notes, setNotes] = useState<Record<number, string>>({});

  if (!questions) return null;

  if (answered) {
    return (
      <div data-testid="ask-user-answered" style={{ opacity: 0.8 }}>
        {answered}
      </div>
    );
  }

  const single = questions.length === 1;

  const send = (chosen: Record<number, string>) => {
    const lines = questions.map((q, i) => answerLine(q.question, chosen[i], notes[i] ?? ""));
    onAnswer({ content: lines.join("\n"), answers: call.call_id });
  };

  return (
    <div data-testid="ask-user" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {questions.map((q, i) => (
        <div key={i} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontWeight: 600 }}>{q.question}</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {q.options.map((opt) => {
              const active = picked[i] === opt.label;
              return (
                <button
                  key={opt.label}
                  type="button"
                  aria-pressed={active}
                  onClick={() => {
                    // A single question has nothing to wait for — one click is
                    // the whole answer, so sending it immediately saves the
                    // user a pointless second click.
                    if (single) send({ [i]: opt.label });
                    else setPicked((p) => ({ ...p, [i]: opt.label }));
                  }}
                  title={opt.description || undefined}
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "flex-start",
                    gap: 2,
                    padding: "6px 10px",
                    textAlign: "left",
                    borderWidth: active ? 2 : 1,
                    borderStyle: "solid",
                    borderRadius: 6,
                    cursor: "pointer",
                  }}
                >
                  <span>{opt.label}</span>
                  {opt.description ? (
                    <span style={{ opacity: 0.7, fontSize: "0.85em" }}>{opt.description}</span>
                  ) : null}
                </button>
              );
            })}
          </div>
          {/* Added by the card, never by the agent — so the way out exists even
              when a small model forgets to offer one. "看不懂" is a rejection of
              the question, not an answer to it: the agent should re-ask in
              plainer words rather than proceed on a choice never made. */}
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button
              type="button"
              onClick={() => {
                if (single) send({ [i]: DONT_UNDERSTAND });
                else setPicked((p) => ({ ...p, [i]: DONT_UNDERSTAND }));
              }}
              style={{ padding: "4px 8px", cursor: "pointer", flexShrink: 0, whiteSpace: "nowrap" }}
            >
              看不懂
            </button>
            <input
              type="text"
              value={notes[i] ?? ""}
              placeholder="補充,或自己回答"
              onChange={(e) => setNotes((n) => ({ ...n, [i]: e.target.value }))}
              style={{ flex: 1, minWidth: 0, padding: "4px 8px" }}
            />
            {single ? (
              <button
                type="button"
                onClick={() => send(picked)}
                style={{ padding: "4px 8px", flexShrink: 0, whiteSpace: "nowrap" }}
              >
                送出
              </button>
            ) : null}
          </div>
        </div>
      ))}
      {single ? null : (
        // Sending without answering everything is allowed on purpose: forcing
        // a full card makes people pick something to escape it, and an invented
        // preference is worse than a missing one. The unanswered questions are
        // reported as unanswered so the agent knows what it still lacks.
        // The primary action has to read as the primary action: unstyled it came
        // out looking plainer than 看不懂 beside it, so the one button the user
        // must press was the least button-like thing on the card.
        <button
          type="button"
          onClick={() => send(picked)}
          style={{
            alignSelf: "flex-start",
            padding: "6px 14px",
            fontWeight: 600,
            borderWidth: 2,
            borderStyle: "solid",
            borderRadius: 6,
            cursor: "pointer",
          }}
        >
          送出
        </button>
      )}
    </div>
  );
}
