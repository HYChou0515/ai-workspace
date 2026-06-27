// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import { itemChatApi, type ItemChat, type ItemChatSummary } from "../api/itemChats";
import { kbApi } from "../api/kb";
import { workflowApi, type ProfileDTO, type WorkflowRunDTO } from "../api/workflows";
import type { FileContent } from "../api/types";
import { renderWithQuery } from "../test/queryWrapper";
import { ItemChatShell } from "./ItemChatShell";

// The active chat now renders the real RCA AgentPanel (model picker, kbApi, dialogs
// …). Stub it so these tests stay focused on the multi-chat chrome (tab rail /
// picker / workflow launch / Continue gate) — AgentPanel has its own tests. The
// stub forwards `onNewChat` (#200) so we can assert the escape hatch is threaded
// into the chat header exactly when the shell bar is hidden.
vi.mock("../pages/investigation/AgentPanel", () => ({
  AgentPanel: ({ onNewChat }: { onNewChat?: () => void }) => (
    <div data-testid="agent-panel-stub">
      {onNewChat ? (
        <button type="button" data-testid="header-new-chat" onClick={onNewChat}>
          + New chat
        </button>
      ) : null}
    </div>
  ),
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
  name_hint: "",
  status: null,
  last_activity_ms: null,
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

/** Make the collection-set read resolve to a given collections.json body, or a
 * 404 (missing file) when omitted. Keeps the shell's badge query from erroring. */
function stubCollectionsFile(body?: string) {
  vi.spyOn(api, "readFile").mockImplementation(async (_slug, _id, path): Promise<FileContent> => {
    if (path === "/collections.json" && body !== undefined) {
      return { kind: "text", path, size: body.length, text: body, encoding: "utf-8" };
    }
    const err = new Error("read failed: 404") as Error & { status: number };
    err.status = 404;
    throw err;
  });
  vi.spyOn(kbApi, "listCollections").mockResolvedValue([]);
}

beforeEach(() => {
  vi.spyOn(workflowApi, "listProfiles").mockResolvedValue(PROFILES);
  stubCollectionsFile();
});

// Defaults mirror Topic Hub's manifest (always-on switcher + a collection set),
// so the pre-#200 tests keep seeing the full chrome. #200 tests override these to
// the single-chat-leaning shape (chatSwitcher="auto", showCollections=false).
const render = (over: { chatSwitcher?: "auto" | "always"; showCollections?: boolean } = {}) =>
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
      uploadDir="uploads"
      chatSwitcher={over.chatSwitcher ?? "always"}
      showCollections={over.showCollections ?? true}
    />,
  );

// A profile with no workflows — the RCA-shaped case where the only reason to show
// the shell bar would be the switcher or collections.
const NO_WORKFLOW_PROFILES: ProfileDTO[] = [
  { name: "default", title: "Default", description: "", has_workflow: false, workflow: null, workflows: [] },
];

describe("ItemChatShell", () => {
  it("renders the chat switcher and a single New picker listing the profile's workflows", async () => {
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    render();
    await waitFor(() => expect(screen.getByTestId("chat-switcher-trigger")).toBeInTheDocument());
    // The merged "+ New" picker offers Free chat + the profile's workflows.
    fireEvent.click(await screen.findByTestId("new-item-button"));
    expect(screen.getByTestId("new-item-free")).toBeInTheDocument();
    expect(await screen.findByText("Digest uploads into memory")).toBeInTheDocument();
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

  it("opens a free chat via the New picker (createChat) and selects it", async () => {
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    const created = summary({ chat_id: "conversation:free2", title: "side", is_default: false });
    const create = vi.spyOn(itemChatApi, "createChat").mockResolvedValue(created);
    render();
    await waitFor(() => expect(screen.getByTestId("new-item-button")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("new-item-button"));
    fireEvent.click(screen.getByTestId("new-item-free"));
    await waitFor(() => expect(create).toHaveBeenCalledWith("topic-hub", "it", ""));
  });

  it("launches a workflow via the New picker — pre-flight dialog first, then startRun", async () => {
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    vi.spyOn(workflowApi, "previewRun").mockResolvedValue({
      workflow_id: "collections",
      title: "File uploads into collections",
      description: "",
      phases: [{ id: "classify", title: "Classify" }],
      summary: "把 1 個檔案歸檔",
      checks: [],
      can_run: true,
      has_preflight: true,
    });
    const start = vi
      .spyOn(workflowApi, "startRun")
      .mockResolvedValue({ run_id: "r1", item_id: "it", chat_id: "conversation:wf1" });
    render();
    await waitFor(() => expect(screen.getByTestId("new-item-button")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("new-item-button"));
    fireEvent.click(await screen.findByTestId("new-item-workflow-collections"));
    // the dialog opens first; nothing starts until the operator confirms
    await screen.findByTestId("wf-launch-dialog");
    expect(start).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("wf-launch-run"));
    await waitFor(() => expect(start).toHaveBeenCalledWith("topic-hub", "it", "collections"));
  });

  it("nudges to pick collections when the hub has none, and opens the picker modal", async () => {
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    stubCollectionsFile(); // 404 → empty selection
    render();
    const button = await screen.findByTestId("collections-button");
    expect(button).toHaveTextContent("設定搜尋範圍");
    fireEvent.click(button);
    expect(await screen.findByTestId("collections-modal")).toBeInTheDocument();
  });

  it("badges the collection count from collections.json", async () => {
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    stubCollectionsFile('[{"id":"a","name":"Alpha"},{"id":"b","name":"Beta"}]');
    render();
    await waitFor(() =>
      expect(screen.getByTestId("collections-button")).toHaveTextContent("搜尋範圍 · 2"),
    );
  });

  it("opens the manage modal from the switcher and deletes a chat", async () => {
    stubChatApi([
      summary({ chat_id: "conversation:c1", is_default: true }),
      summary({ chat_id: "conversation:c2", title: "Side", is_default: false }),
    ]);
    const del = vi.spyOn(itemChatApi, "deleteChat").mockResolvedValue();
    render();
    await waitFor(() => expect(screen.getByTestId("chat-switcher-trigger")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("chat-switcher-trigger"));
    fireEvent.click(screen.getByTestId("chat-switcher-manage"));
    expect(await screen.findByTestId("manage-chats-modal")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("manage-delete-conversation:c2"));
    fireEvent.click(screen.getByTestId("manage-delete-confirm-conversation:c2"));
    await waitFor(() =>
      expect(del).toHaveBeenCalledWith("topic-hub", "it", "conversation:c2"),
    );
  });

  it("hides the whole bar for a single-chat-leaning App (auto, 1 chat, no collections, no workflows) and threads the escape hatch into the chat header (#200)", async () => {
    vi.spyOn(workflowApi, "listProfiles").mockResolvedValue(NO_WORKFLOW_PROFILES);
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    render({ chatSwitcher: "auto", showCollections: false });
    await waitFor(() => expect(screen.getByTestId("agent-panel-stub")).toBeInTheDocument());
    // No multi-chat chrome: switcher, "+ New" picker and collections are all absent.
    expect(screen.queryByTestId("chat-switcher-trigger")).not.toBeInTheDocument();
    expect(screen.queryByTestId("new-item-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("collections-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("item-chat-shell__bar")).not.toBeInTheDocument();
    // The lone escape hatch is threaded into the chat header instead.
    expect(screen.getByTestId("header-new-chat")).toBeInTheDocument();
  });

  it("creates a fresh chat from the header escape hatch when the bar is hidden (#200)", async () => {
    vi.spyOn(workflowApi, "listProfiles").mockResolvedValue(NO_WORKFLOW_PROFILES);
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    const create = vi
      .spyOn(itemChatApi, "createChat")
      .mockResolvedValue(summary({ chat_id: "conversation:c2", is_default: false }));
    render({ chatSwitcher: "auto", showCollections: false });
    fireEvent.click(await screen.findByTestId("header-new-chat"));
    await waitFor(() => expect(create).toHaveBeenCalledWith("topic-hub", "it", ""));
  });

  it("surfaces the switcher and drops the header escape hatch once a second chat exists in auto mode (#200)", async () => {
    vi.spyOn(workflowApi, "listProfiles").mockResolvedValue(NO_WORKFLOW_PROFILES);
    stubChatApi([
      summary({ chat_id: "conversation:c1", is_default: true }),
      summary({ chat_id: "conversation:c2", title: "Side", is_default: false }),
    ]);
    render({ chatSwitcher: "auto", showCollections: false });
    // Two chats → the bar appears with the switcher, so the user can hop back to
    // the wedged chat or stay on the fresh one; the header hatch is now redundant.
    await waitFor(() => expect(screen.getByTestId("chat-switcher-trigger")).toBeInTheDocument());
    expect(screen.getByTestId("item-chat-shell__bar")).toBeInTheDocument();
    expect(screen.queryByTestId("header-new-chat")).not.toBeInTheDocument();
  });

  it("shows the bar for an App with workflows but keeps the switcher hidden in auto mode with one chat (#200)", async () => {
    // PROFILES (beforeEach) carries workflows → the bar must show its New picker,
    // but with a single chat and `auto` the switcher and header hatch stay away.
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    render({ chatSwitcher: "auto", showCollections: false });
    await waitFor(() => expect(screen.getByTestId("new-item-button")).toBeInTheDocument());
    expect(screen.getByTestId("item-chat-shell__bar")).toBeInTheDocument();
    expect(screen.queryByTestId("chat-switcher-trigger")).not.toBeInTheDocument();
    expect(screen.queryByTestId("collections-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("header-new-chat")).not.toBeInTheDocument();
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
      steps: [],
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
    // The gate is pinned to the top of the chat (#170) so scrolling past the
    // feed can't bury the decision.
    expect(screen.getByTestId("workflow-continue")).toHaveStyle({ position: "sticky" });
    expect(screen.getByText("Filled in the glossary? Continue to commit?")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() => expect(decide).toHaveBeenCalledWith("topic-hub", "it", "r1", { choice: "approve" }));
  });
});
