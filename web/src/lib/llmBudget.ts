/**
 * Ask for the LLM budget again because a turn just ended — money was just spent
 * (docs/plan-llm-budget.md D6). Called by every chat's turn end: the item chat
 * (`useChatSession`) and the knowledge-base chat (`useKbChat`).
 *
 * The server keeps one answer per person for a window (30 s, per pod), so a turn
 * that ends inside it is answered with the figure from BEFORE the turn. The
 * answer says how long until the server asks the deploy's class again
 * (`stale_in_s`); this asks once more then — the value comes from the server
 * that holds the window, not from a second copy of it here. One pending ask per
 * client: a later turn end replaces it. No polling beyond that one ask.
 */
import type { QueryClient } from "@tanstack/react-query";

import { qk } from "../api/queryKeys";
import type { LlmBudget } from "../api/types";

type Timer = { set: (fn: () => void, ms: number) => unknown; clear: (handle: unknown) => void };
const realTimer: Timer = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

const pending = new WeakMap<QueryClient, unknown>();

export async function refreshBudgetAfterTurn(qc: QueryClient, timer: Timer = realTimer): Promise<void> {
  const before = pending.get(qc);
  if (before !== undefined) {
    timer.clear(before);
    pending.delete(qc);
  }
  await qc.invalidateQueries({ queryKey: qk.llmBudget });
  const shown = qc.getQueryData<LlmBudget>(qk.llmBudget);
  const staleIn = shown?.stale_in_s ?? 0;
  if (staleIn > 0) {
    pending.set(
      qc,
      timer.set(() => {
        pending.delete(qc);
        void qc.invalidateQueries({ queryKey: qk.llmBudget });
      }, staleIn * 1000),
    );
  }
}
