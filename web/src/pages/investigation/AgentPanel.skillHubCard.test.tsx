// @vitest-environment happy-dom
/**
 * plan-skill-hub-history A3: a `show_skill_hub_entry` card in an item's chat
 * acts on THAT item — the panel hands its item to the log. A read-only
 * viewer's panel hands none, so the card shows without an install.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import { kbApi } from "../../api/kb";
import { DialogProvider } from "../../components/Dialog";
import { useChatItem } from "../../hooks/chatItem";
import type { AgentState } from "../../hooks/useAgent";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { renderWithQuery } from "../../test/queryWrapper";
import { AgentPanel } from "./AgentPanel";

vi.mock("../../components/SkillHubEntryCard", () => ({
  SkillHubEntryCard: ({ entryId }: { entryId: string }) => {
    const item = useChatItem();
    return <div data-testid="card-item">{`${entryId} ${JSON.stringify(item)}`}</div>;
  },
}));

function agent(): AgentState {
  return {
    investigationId: "it1",
    log: {
      entries: [
        {
          kind: "tool_call",
          call: {
            call_id: "c1",
            name: "show_skill_hub_entry",
            status: "done",
            args: { entry_id: "e-1" },
            output: 'shown.\n[skill-hub-entry]{"entry_id":"e-1"}',
          },
        },
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

function renderPanel(readOnly: boolean) {
  renderWithQuery(
    <MemoryRouter>
      <DialogProvider>
        <WorkspaceSlugProvider value="pm">
          <AgentPanel
            investigationId="it1"
            chatId="chat-1"
            agent={agent()}
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
});

afterEach(cleanup);

describe("AgentPanel — a skill hub card in the log", () => {
  it("is handed the panel's item", () => {
    renderPanel(false);
    expect(screen.getByTestId("card-item")).toHaveTextContent('e-1 {"slug":"pm","itemId":"it1"}');
  });

  it("is handed no item when the viewer may only read the chat", () => {
    renderPanel(true);
    expect(screen.getByTestId("card-item")).toHaveTextContent("e-1 null");
  });
});
