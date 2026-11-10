import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { qk } from "../api/queryKeys";
import type { LlmBudget } from "../api/types";
import { refreshBudgetAfterTurn } from "./llmBudget";

// docs/plan-llm-budget.md D6 + review round 1 (#1015): a turn that ends inside
// the server's 30 s window is answered from its cache — the figure from BEFORE
// the turn. The page asks now, and once more when the server says its window
// ends (`stale_in_s`), so the post-turn figure shows without any polling.
function shownBudget(staleIn: number) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 } } });
  const queryFn = vi.fn(async (): Promise<LlmBudget> => ({ status: "ok", budget: null, stale_in_s: staleIn }));
  new QueryObserver(qc, { queryKey: qk.llmBudget, queryFn }).subscribe(() => {});
  return { qc, queryFn };
}

/** A timer the test fires by hand, recording each delay asked for. */
function manualTimer() {
  const armed: { fn: () => void; ms: number; live: boolean }[] = [];
  return {
    armed,
    set: (fn: () => void, ms: number) => {
      const t = { fn, ms, live: true };
      armed.push(t);
      return t;
    },
    clear: (handle: unknown) => {
      (handle as { live: boolean }).live = false;
    },
    fire: () => armed.filter((t) => t.live).forEach((t) => ((t.live = false), t.fn())),
  };
}

describe("refreshBudgetAfterTurn", () => {
  it("asks now, then once more when the server's window ends — no further", async () => {
    const { qc, queryFn } = shownBudget(18);
    await vi.waitFor(() => expect(qc.getQueryData(qk.llmBudget)).toBeDefined());
    const timer = manualTimer();
    await refreshBudgetAfterTurn(qc, timer);
    expect(queryFn).toHaveBeenCalledTimes(2);
    expect(timer.armed.map((t) => t.ms)).toEqual([18_000]);
    timer.fire();
    await vi.waitFor(() => expect(queryFn).toHaveBeenCalledTimes(3));
    // The follow-up ask is not itself followed: no polling.
    expect(timer.armed).toHaveLength(1);
  });

  it("a second turn replaces the pending ask rather than adding one", async () => {
    const { qc } = shownBudget(18);
    await vi.waitFor(() => expect(qc.getQueryData(qk.llmBudget)).toBeDefined());
    const timer = manualTimer();
    await refreshBudgetAfterTurn(qc, timer);
    await refreshBudgetAfterTurn(qc, timer);
    expect(timer.armed.filter((t) => t.live)).toHaveLength(1);
  });

  it("schedules nothing when the server will not ask again, or nothing is shown", async () => {
    const off = shownBudget(0);
    await vi.waitFor(() => expect(off.qc.getQueryData(qk.llmBudget)).toBeDefined());
    const timer = manualTimer();
    await refreshBudgetAfterTurn(off.qc, timer);
    expect(timer.armed).toHaveLength(0);

    await refreshBudgetAfterTurn(new QueryClient(), timer); // no budget on screen
    expect(timer.armed).toHaveLength(0);
  });
});
