// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { KbApi, KbReviewInbox } from "../api/kb";
import { QueryWrap } from "../test/queryWrapper";
import { useReviewBadgeCount } from "./useReviewInbox";

function fakeClient(inbox: KbReviewInbox): KbApi {
  return { getReviewInbox: vi.fn().mockResolvedValue(inbox) } as unknown as KbApi;
}

const card = (can_act: boolean) => ({
  run_id: "r",
  collection_id: "c",
  collection_name: "C",
  can_act,
  created_time: 0,
  card: {
    id: "0",
    keys: ["K"],
    title: "K",
    body: "",
    confident: true,
    mode: "new" as const,
    target_card_id: null,
    provenance: [],
    decision: "pending" as const,
  },
});


describe("useReviewBadgeCount", () => {
  it("reads the server's total_actionable (not the loaded page)", async () => {
    // The badge asks for a one-row page but the count comes from total_actionable,
    // so it reflects the WHOLE backlog without pulling it (#506 G2).
    const client = fakeClient({ cards: [card(true)], questions: [], total_actionable: 2 });
    const { result } = renderHook(() => useReviewBadgeCount(client), { wrapper: QueryWrap });
    await waitFor(() => expect(result.current).toBe(2));
    expect(client.getReviewInbox).toHaveBeenCalledWith({ limit: 1 });
  });

  it("is 0 while loading / when nothing is actionable", async () => {
    const client = fakeClient({ cards: [], questions: [], total_actionable: 0 });
    const { result } = renderHook(() => useReviewBadgeCount(client), { wrapper: QueryWrap });
    expect(result.current).toBe(0); // loading → 0
    await waitFor(() => expect(client.getReviewInbox).toHaveBeenCalled());
    expect(result.current).toBe(0); // resolved but no actionable items
  });
});
