// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { itemChatApi, type ItemChat, type ItemChatSummary } from "../api/itemChats";
import { workflowApi, type ProfileDTO, type WorkflowRunDTO } from "../api/workflows";
import { renderWithQuery } from "../test/queryWrapper";
import { ItemChatShell } from "./ItemChatShell";

// The active chat now renders the real RCA AgentPanel (model picker, kbApi, dialogs
// …). Stub it so these tests stay focused on the multi-chat chrome (tab rail /
// picker / workflow launch / Continue gate) — AgentPanel has its own tests.
vi.mock("../pages/investigation/AgentPanel", () => ({
  AgentPanel: () => <div data-testid="agent-panel-stub" />,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const summary = (over: Partial<ItemChatSummary>): ItemChatSummary => ({
  chat_id: "conversation:c1",
  title: "",
  run_id: null,
  created_ms: null,
  message_count: 0,
  is_default: true,
  ...over,
});

const thread = (over: Partial<ItemChat> = {}): ItemChat => ({
  chatId: "conversation:c1",
  title: "",
  runId: null,
  messages: [],
  ...over,
});

const PROFILES: ProfileDTO[] = [
  {
    name: "default",
    title: "Default",
    description: "",
    has_workflow: true,
    workflow: null,
    workflows: [
      { id: "memory", title: "Digest uploads into memory", phases: [], input_json: "x" },
      { id: "collections", title: "File uploads into collections", phases: [], input_json: "x" },
    ],
  },
];

function stubChatApi(chats: ItemChatSummary[]) {
  vi.spyOn(itemChatApi, "listChats").mockResolvedValue(chats);
  vi.spyOn(itemChatApi, "getChat").mockResolvedValue(thread());
  vi.spyOn(itemChatApi, "cancelMessage").mockResolvedValue();
  vi.spyOn(itemChatApi, "sendMessage").mockResolvedValue();
  // hang the per-chat subscription so the panel stays mounted in steady state.
  vi.spyOn(itemChatApi, "subscribe").mockImplementation(
    () =>
      (async function* () {
        await new Promise<void>(() => {});
      })(),
  );
}

beforeEach(() => {
  vi.spyOn(workflowApi, "listProfiles").mockResolvedValue(PROFILES);
});

const render = () =>
  renderWithQuery(
    <ItemChatShell
      slug="topic-hub"
      itemId="it"
      profile="default"
      picker={[]}
      suggestions={[]}
      appTitle="Topic Hub"
      attachedPreset=""
      onAttachPreset={() => {}}
    />,
  );

describe("ItemChatShell", () => {
  it("renders a tab per chat and the new-chat picker with the profile's workflows", async () => {
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    render();
    await waitFor(() => expect(screen.getByTestId("chat-tab-conversation:c1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("new-chat-button"));
    expect(screen.getByRole("menuitem", { name: "Free chat" })).toBeInTheDocument();
    // workflows arrive once listProfiles resolves — findBy retries.
    expect(
      await screen.findByRole("menuitem", { name: "Digest uploads into memory" }),
    ).toBeInTheDocument();
  });

  it("auto-opens a default free chat when the hub has none yet", async () => {
    const created = summary({ chat_id: "conversation:auto", is_default: true });
    // First load: empty hub. After createChat invalidates, the chat is listed.
    vi.spyOn(itemChatApi, "listChats").mockResolvedValueOnce([]).mockResolvedValue([created]);
    vi.spyOn(itemChatApi, "getChat").mockResolvedValue(thread({ chatId: "conversation:auto" }));
    vi.spyOn(itemChatApi, "cancelMessage").mockResolvedValue();
    vi.spyOn(itemChatApi, "sendMessage").mockResolvedValue();
    vi.spyOn(itemChatApi, "subscribe").mockImplementation(
      () =>
        (async function* () {
          await new Promise<void>(() => {});
        })(),
    );
    const create = vi.spyOn(itemChatApi, "createChat").mockResolvedValue(created);
    render();
    // The shell creates the implicit default chat instead of stalling on the
    // empty placeholder, and lands on a usable chat panel.
    await waitFor(() => expect(create).toHaveBeenCalledWith("topic-hub", "it", ""));
    await waitFor(() => expect(screen.getByTestId("agent-panel-stub")).toBeInTheDocument());
  });

  it("opens a free chat via the picker (createChat) and selects it", async () => {
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    const created = summary({ chat_id: "conversation:free2", title: "side", is_default: false });
    const create = vi.spyOn(itemChatApi, "createChat").mockResolvedValue(created);
    render();
    await waitFor(() => expect(screen.getByTestId("new-chat-button")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("new-chat-button"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Free chat" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith("topic-hub", "it", ""));
  });

  it("launches a workflow via the picker (startRun with the workflow id)", async () => {
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    const start = vi
      .spyOn(workflowApi, "startRun")
      .mockResolvedValue({ run_id: "r1", item_id: "it", chat_id: "conversation:wf1" });
    render();
    await waitFor(() => expect(screen.getByTestId("new-chat-button")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("new-chat-button"));
    fireEvent.click(await screen.findByRole("menuitem", { name: "File uploads into collections" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith("topic-hub", "it", "collections"));
  });

  it("shows a Continue affordance on a paused workflow chat and decides on click", async () => {
    // The only chat is a workflow chat → auto-selected; its run is awaiting_human.
    stubChatApi([summary({ chat_id: "conversation:wf1", run_id: "r1", is_default: false, title: "Run" })]);
    const run: WorkflowRunDTO = {
      run_id: "r1",
      item_id: "it",
      captured_user: "u",
      status: "awaiting_human",
      current_phase: "review",
      phases: [],
      failures: [],
      started: 1,
      ended: null,
      result: null,
      pending_decision: {
        phase: "review",
        title: "Filled in the glossary? Continue to commit?",
        summary: "",
        allow: ["approve", "reject"],
        decided_by: "",
      },
    };
    vi.spyOn(workflowApi, "getRun").mockResolvedValue(run);
    const decide = vi.spyOn(workflowApi, "decide").mockResolvedValue();
    render();
    await waitFor(() => expect(screen.getByTestId("workflow-continue")).toBeInTheDocument());
    expect(screen.getByText("Filled in the glossary? Continue to commit?")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("gate-approve"));
    await waitFor(() => expect(decide).toHaveBeenCalledWith("topic-hub", "it", "r1", { choice: "approve" }));
  });
});
