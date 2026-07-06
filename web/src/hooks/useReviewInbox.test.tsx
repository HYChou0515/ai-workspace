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

const question = (can_act: boolean) => ({
  collection_id: "c",
  collection_name: "C",
  can_act,
  created_time: 0,
  question: {
    id: "q",
    collection_id: "c",
    kind: "term" as const,
    status: "open",
    question_text: "?",
    term: "T",
    source_doc_ids: [],
    source_doc_id: "",
    quote: "",
  },
});

describe("useReviewBadgeCount", () => {
  it("counts only the pending items the user can act on", async () => {
    const client = fakeClient({
      cards: [card(true), card(false)],
      questions: [question(true)],
    });
    const { result } = renderHook(() => useReviewBadgeCount(client), { wrapper: QueryWrap });
    await waitFor(() => expect(result.current).toBe(2)); // one actionable card + one question
  });

  it("is 0 while loading / when nothing is actionable", async () => {
    const client = fakeClient({ cards: [card(false)], questions: [] });
    const { result } = renderHook(() => useReviewBadgeCount(client), { wrapper: QueryWrap });
    expect(result.current).toBe(0); // loading → 0
    await waitFor(() => expect(client.getReviewInbox).toHaveBeenCalled());
    expect(result.current).toBe(0); // resolved but no actionable items
  });
});
