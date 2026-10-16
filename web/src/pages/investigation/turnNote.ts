import type { AgentLog } from "./agentLog";

/** What the composer says while a turn runs (docs/plan-interjection.md).
 *
 * - `own`: your turn is running. A message sent now is read after the AI's
 *   current step (Q1) — and the turn's reasoning effort and disclosure were
 *   settled when it started, so changing them now does not reach it (Q7).
 * - `others`: someone else's turn is running. A turn runs as one person, so a
 *   message sent now stops it and yours takes over (Q2) — said before you send,
 *   not discovered after.
 *
 * Shared by both chat panels so they cannot come to say different things. */
export type TurnNote = { kind: "own" } | { kind: "others"; who: string };

export function turnNote(log: AgentLog, me: string | null | undefined): TurnNote | null {
  if (!log.streaming || log.streamingBy == null) return null;
  // Not yet known who is signed in: "someone else's turn" would be a guess, and
  // for your own turn a wrong one — say nothing until it is known.
  if (me == null) return null;
  if (log.streamingBy === me) return { kind: "own" };
  return { kind: "others", who: log.streamingBy };
}
