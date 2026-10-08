// @vitest-environment happy-dom
/**
 * docs/plan-outside-lookup.md: a "請幫我查" card in an item's chat answers into
 * THAT chat, retires once a message answers it, and — in a panel without
 * permission to chat — shows no actions.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import { kbApi } from "../../api/kb";
import { outsideLookupApi } from "../../api/outsideLookup";
import { DialogProvider } from "../../components/Dialog";
import type { AgentState } from "../../hooks/useAgent";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { renderWithQuery } from "../../test/queryWrapper";
import { AgentPanel } from "./AgentPanel";

function agent(extra: unknown[] = []): AgentState {
  return {
    investigationId: "it1",
    log: {
      entries: [
        {
          kind: "tool_call",
          call: {
            call_id: "c1",
            name: "ask_outside",
            status: "done",
            args: {},
            output: 'Asked.\n[outside-lookup]{"why":"need the 2.0 notes","query":"pandas 2.0"}',
          },
        },
        ...extra,
      ],
      streaming: false,
    } as unknown as AgentState["log"],
    connection: { state: "live", receiving: true, error: null, attempts: 0 },
    send: vi.fn(async () => {}),
    mention: vi.fn(async () => {}),
    cancel: vi.fn(),
    undo: vi.fn(async () => {}),
  };
}

function renderPanel(a: AgentState, { readOnly = false }: { readOnly?: boolean } = {}) {
  renderWithQuery(
    <MemoryRouter>
      <DialogProvider>
        <WorkspaceSlugProvider value="pm">
          <AgentPanel
            investigationId="it1"
            chatId="chat-1"
            agent={a}
            picker={[]}
            suggestions={[]}
            attachedPreset=""
            onAttachPreset={() => {}}
            uploadDir="uploads"
            readOnly={readOnly}
          />
        </WorkspaceSlugProvider>
      </DialogProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(kbApi, "listCollections").mockResolvedValue([]);
  vi.spyOn(api, "getWorkspaceUsage").mockResolvedValue({ used: 0, quota: 0 });
  vi.spyOn(api, "getItemTools").mockResolvedValue({ tools: [], updateNeedsClose: false, canClose: false });
  vi.spyOn(api, "getEnvProviders").mockResolvedValue([]);
  vi.spyOn(outsideLookupApi, "targets").mockResolvedValue([
    { name: "Google", url: "https://www.google.com/search?q={q}" },
  ]);
});

afterEach(cleanup);

describe("AgentPanel — a 請幫我查 card in the log", () => {
  it("answers into this chat of this item", async () => {
    const answer = vi
      .spyOn(outsideLookupApi, "answer")
      .mockResolvedValue({ path: "lookups/a.md", attachments: [] });
    renderPanel(agent());

    fireEvent.change(await screen.findByLabelText("查到的內容"), { target: { value: "found" } });
    fireEvent.click(screen.getByRole("button", { name: "送出" }));

    await waitFor(() => expect(answer).toHaveBeenCalledTimes(1));
    expect(answer.mock.calls[0]![0]).toMatchObject({
      slug: "pm",
      itemId: "it1",
      chatId: "chat-1",
      callId: "c1",
    });
  });

  it("is retired once a message in the thread answers it", async () => {
    const answered = {
      kind: "message",
      message: { role: "user", content: "我在外面查了:pandas 2.0", answers: "c1" },
    };
    renderPanel(agent([answered]));

    expect(await screen.findByText("已回覆")).toBeInTheDocument();
    expect(screen.queryByLabelText("查到的內容")).toBeNull();
  });

  it("shows the request without actions to a read-only viewer", async () => {
    renderPanel(agent(), { readOnly: true });

    expect(await screen.findByText("need the 2.0 notes")).toBeInTheDocument();
    expect(screen.queryByLabelText("查到的內容")).toBeNull();
  });
});
