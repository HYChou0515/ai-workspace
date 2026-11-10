// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import { kbApi } from "../../api/kb";
import { DialogProvider } from "../../components/Dialog";
import type { AgentState } from "../../hooks/useAgent";
import { translate } from "../../lib/i18n";
import { renderWithQuery } from "../../test/queryWrapper";
import { AgentPanel } from "./AgentPanel";

// docs/plan-llm-budget.md P3: the budget sits in the row above the item chat's
// composer, beside storage and context — where the money is spent.
function stubAgent(): AgentState {
  return {
    investigationId: "it1",
    log: { entries: [], streaming: false } as unknown as AgentState["log"],
    connection: { state: "live", receiving: true, error: null, attempts: 0 },
    send: vi.fn(async () => {}),
    mention: vi.fn(async () => {}),
    cancel: vi.fn(),
    undo: vi.fn(async () => {}),
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(kbApi, "listCollections").mockResolvedValue([]);
  vi.spyOn(api, "getWorkspaceUsage").mockResolvedValue({ used: 0, quota: 0 });
});
afterEach(cleanup);

describe("AgentPanel LLM budget", () => {
  it("shows the asking person's budget in the composer's status row", async () => {
    vi.spyOn(api, "getLlmBudget").mockResolvedValue({
      status: "ok",
      budget: { remaining: 11, total: 20, currency: "USD", message: "", link: null, tone: "info" },
    });
    renderWithQuery(
      <MemoryRouter>
        <DialogProvider>
          <AgentPanel
            investigationId="it1"
            chatId="chat-1"
            agent={stubAgent()}
            picker={[]}
            suggestions={[]}
            attachedPreset=""
            onAttachPreset={() => {}}
            uploadDir="uploads"
          />
        </DialogProvider>
      </MemoryRouter>,
    );
    const row = await screen.findByTestId("composer-status");
    expect(await within(row).findByTestId("llm-budget")).toHaveTextContent(
      translate("zh-TW", "budget.remainingOfTotal", { remaining: "$11", total: "$20" }),
    );
  });
});
