// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import type { KbApi } from "../../api/kb";
import { mockKbApi } from "../../api/kbMock";
import { translate } from "../../lib/i18n";
import { QueryWrap } from "../../test/queryWrapper";
import { KbChatPanel } from "./KbChatPanel";

// docs/plan-llm-budget.md P4 (D9): the knowledge-base chat spends LLM money
// too, so its composer carries the same row as the item chat's — the same
// component, the same per-person answer.
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("KbChatPanel LLM budget", () => {
  it("shows the asking person's budget above the composer", async () => {
    vi.spyOn(api, "getLlmBudget").mockResolvedValue({
      status: "ok",
      budget: { remaining: 7, total: 10, currency: "USD", message: "", link: null, tone: "info" },
    });
    const client: KbApi = { ...mockKbApi, listCollections: async () => [], listChats: async () => [] };
    render(<KbChatPanel chatId={null} client={client} />, { wrapper: QueryWrap });
    const row = await screen.findByTestId("kb-composer-status");
    expect(row).toHaveClass("composer-status");
    expect(await within(row).findByTestId("llm-budget")).toHaveTextContent(
      translate("zh-TW", "budget.remainingOfTotal", { remaining: "$7", total: "$10" }),
    );
  });
});
