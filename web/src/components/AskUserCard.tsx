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
    const lines = questions.map(
      (q, i) => `${q.question} → ${chosen[i] ?? UNANSWERED}`,
    );
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
        </div>
      ))}
      {single ? null : (
        // Sending without answering everything is allowed on purpose: forcing
        // a full card makes people pick something to escape it, and an invented
        // preference is worse than a missing one. The unanswered questions are
        // reported as unanswered so the agent knows what it still lacks.
        <button type="button" onClick={() => send(picked)} style={{ alignSelf: "flex-start" }}>
          送出
        </button>
      )}
    </div>
  );
}
