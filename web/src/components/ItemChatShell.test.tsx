// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import { itemChatApi, type ItemChat, type ItemChatSummary } from "../api/itemChats";
import { kbApi } from "../api/kb";
import { workflowApi, type ProfileDTO, type WorkflowRunDTO } from "../api/workflows";
import { workspaceWorkflowsApi } from "../api/workspaceWorkflows";
import type { FileContent } from "../api/types";
import { qk } from "../api/queryKeys";
import { makeTestQueryClient, renderWithQuery } from "../test/queryWrapper";

import { DialogProvider } from "./Dialog";
import { ItemChatShell } from "./ItemChatShell";

// The active chat now renders the real RCA AgentPanel (model picker, kbApi, dialogs
// …). Stub it so these tests stay focused on the multi-chat chrome (tab rail /
// picker / workflow launch / Continue gate) — AgentPanel has its own tests. The
// stub forwards `onNewChat` (#200) so we can assert the escape hatch is threaded
// into the chat header exactly when the shell bar is hidden.
vi.mock("../pages/investigation/AgentPanel", () => ({
  AgentPanel: ({
    onNewChat,
    canAddFiles,
    ...rest
  }: {
    onNewChat?: () => void;
    canAddFiles?: boolean;
    [prop: string]: unknown;
  }) => (
    <div
      data-testid="agent-panel-stub"
      data-can-add-files={String(canAddFiles)}
      // the composer must never be handed a steer route (plan-workflow-out-of-chat D2)
      data-steers={String("onSteer" in rest)}
    >
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
  vi.spyOn(workspaceWorkflowsApi, "list").mockResolvedValue([]);
  stubCollectionsFile();
});

// Defaults mirror Topic Hub's manifest (always-on switcher + a collection set),
// so the pre-#200 tests keep seeing the full chrome. #200 tests override these to
// the single-chat-leaning shape (chatSwitcher="auto", showCollections=false).
const render = (
  over: { chatSwitcher?: "auto" | "always"; showCollections?: boolean; canAddFiles?: boolean } = {},
) =>
  renderWithQuery(
    <ItemChatShell
      slug="topic-hub"
      itemId="it"
      picker={[]}
      suggestions={[]}
      appTitle="Topic Hub"
      attachedPreset=""
      onAttachPreset={() => {}}
      uploadDir="uploads"
      chatSwitcher={over.chatSwitcher ?? "always"}
      showCollections={over.showCollections ?? true}
      canAddFiles={over.canAddFiles}
    />,
  );

// A profile with no workflows — the RCA-shaped case where the only reason to show
// the shell bar would be the switcher or collections.
const NO_WORKFLOW_PROFILES: ProfileDTO[] = [
  { name: "default", title: "Default", description: "", has_workflow: false, workflow: null, workflows: [] },
];

describe("ItemChatShell", () => {
  // plan-outside-lookup: the 請幫我查 card hides its attachments from a viewer
  // who may not add files — that answer comes from the shell, through here.
  it("hands the active chat's panel the viewer's file permission", async () => {
    const chat = summary({ chat_id: "conversation:c", is_default: true });
    vi.spyOn(itemChatApi, "listChats").mockResolvedValue([chat]);
    vi.spyOn(itemChatApi, "getChat").mockResolvedValue(thread({ chatId: "conversation:c" }));
    vi.spyOn(itemChatApi, "subscribe").mockImplementation(
      () =>
        (async function* () {
          await new Promise<void>(() => {});
        })(),
    );
    render({ canAddFiles: false });

    const panel = await screen.findByTestId("agent-panel-stub");
    expect(panel).toHaveAttribute("data-can-add-files", "false");
  });

  // #613: the pinned todo checklist rides above the feed for the active chat.
  it("mounts the todo checklist panel for the active chat", async () => {
    const { itemTodosApi } = await import("../api/itemTodos");
    vi.spyOn(itemTodosApi, "getTodos").mockResolvedValue([
      { text: "fix bug", status: "in_progress" },
    ]);
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    render();
    await waitFor(() => expect(screen.getByTestId("todo-panel")).toBeInTheDocument());
    expect(await screen.findByText("fix bug")).toBeInTheDocument();
  });

  it("renders the chat switcher and a New picker that offers chats only — no workflow", async () => {
    // plan-workflow-out-of-chat D3 / D7: a workflow is started from the
    // workspace's Workflows sidebar, never from a chat — even with a profile
    // full of them (PROFILES, beforeEach).
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    render();
    await waitFor(() => expect(screen.getByTestId("chat-switcher-trigger")).toBeInTheDocument());
    expect(await screen.findByTestId("new-item-button")).toHaveTextContent("New chat");
    expect(screen.queryByText("Digest uploads into memory")).toBeNull();
    expect(screen.queryByTestId("wf-launch-menu")).toBeNull();
  });

  it("a chat a run once drove shows none of the run and never steers", async () => {
    // A chat #343 took over still has the run's id, and that run sits at a gate
    // with a steer plan pending. None of it belongs in the chat any more (D3):
    // no progress, no gate, no steer card — the execution tab carries them —
    // and the composer is a chat composer, not a steer box (D2).
    stubChatApi([summary({ chat_id: "conversation:c1", run_id: "r1", is_default: true })]);
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
        title: "Continue?",
        summary: "",
        allow: ["approve"],
        decided_by: "",
      },
    };
    const getRun = vi.spyOn(workflowApi, "getRun").mockResolvedValue(run);
    render();
    const panel = await screen.findByTestId("agent-panel-stub");
    expect(panel).toHaveAttribute("data-steers", "false");
    expect(screen.queryByTestId("workflow-continue")).toBeNull();
    expect(screen.queryByTestId("workflow-steer")).toBeNull();
    expect(screen.queryByTestId("workflow-progress")).toBeNull();
    expect(getRun).not.toHaveBeenCalled();
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

  it("offers a retry when auto-opening the first chat fails", async () => {
    // `reopening` is what stops a double-create across the create→refetch gap,
    // and it only clears once a chat is actually LISTED — so a rejected create
    // latched it forever: the effect early-returned on every later render and
    // the item sat on the placeholder permanently, with no error and no way out.
    stubChatApi([]);
    const created = summary({ chat_id: "conversation:late", is_default: true });
    const create = vi
      .spyOn(itemChatApi, "createChat")
      .mockRejectedValueOnce(new Error("create chat failed: 503"))
      .mockResolvedValue(created);
    render();

    // The placeholder is on screen from the first paint, so wait for the FAILURE
    // to land on it rather than for the element itself.
    await waitFor(() => expect(screen.getByTestId("no-chat")).toHaveTextContent("503"));

    vi.spyOn(itemChatApi, "listChats").mockResolvedValue([created]);
    vi.spyOn(itemChatApi, "getChat").mockResolvedValue(thread({ chatId: "conversation:late" }));
    fireEvent.click(screen.getByTestId("retry-open-chat"));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByTestId("agent-panel-stub")).toBeInTheDocument());
  });

  it("opens a free chat via the New picker (createChat) and selects it", async () => {
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    const created = summary({ chat_id: "conversation:free2", title: "side", is_default: false });
    const create = vi.spyOn(itemChatApi, "createChat").mockResolvedValue(created);
    render();
    await waitFor(() => expect(screen.getByTestId("new-item-button")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("new-item-button"));
    await waitFor(() => expect(create).toHaveBeenCalledWith("topic-hub", "it", ""));
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

  it("workflows no longer bring up the bar: a one-chat auto App with workflows reads as a single chat", async () => {
    // PROFILES (beforeEach) carries workflows; they used to put a New picker in
    // the bar. With no workflow in the chat (D3), only the switcher or the
    // collection set does, so the header carries the lone escape hatch.
    stubChatApi([summary({ chat_id: "conversation:c1", is_default: true })]);
    render({ chatSwitcher: "auto", showCollections: false });
    await waitFor(() => expect(screen.getByTestId("header-new-chat")).toBeInTheDocument());
    expect(screen.queryByTestId("item-chat-shell__bar")).toBeNull();
  });

});

describe("the chat the address names (?chat=)", () => {
  // The launcher used to sit on a row of its own beneath the bar — one more
  // row taken from the message area for a control the bar had room for. It is
  // in the bar now, after "+ New": the two are different asks (a new chat vs.
  // this one) and stay two controls, but they share the line.
  it("opens the chat the address names (?chat=), not the most recent one", async () => {
    // The schedules overview links a schedule's last run to its own chat
    // (docs/plan-schedule-overview.md); without this the link landed on
    // whichever chat was most recent.
    stubChatApi([
      summary({ chat_id: "conversation:c1", is_default: true, title: "A" }),
      summary({ chat_id: "wui:it:sched", is_default: false, title: "B" }),
    ]);
    window.history.pushState({}, "", "/a/topic-hub/it?chat=wui%3Ait%3Asched");
    try {
      render();
      await waitFor(() => expect(screen.getByTestId("chat-switcher-trigger")).toHaveTextContent("B"));
    } finally {
      window.history.pushState({}, "", "/");
    }
  });

  it("keeps the chat the address names while a cached list is being refreshed", async () => {
    // Review round 1: the item was visited earlier, so its chat list is cached
    // from before the schedule's chat existed. The fallback effect ran against
    // that stale list, did not find the id and picked the newest chat — and
    // the refetch that brought the schedule's chat arrived too late.
    const a = summary({ chat_id: "conversation:c1", is_default: true, title: "A" });
    const b = summary({ chat_id: "wui:it:sched", is_default: false, title: "B" });
    stubChatApi([a, b]);
    const client = makeTestQueryClient();
    client.setQueryData(qk.itemChats("topic-hub", "it"), [a], { updatedAt: Date.now() - 60_000 });
    window.history.pushState({}, "", "/a/topic-hub/it?chat=wui%3Ait%3Asched");
    try {
      renderWithQuery(
        <ItemChatShell
          slug="topic-hub"
          itemId="it"
              picker={[]}
          suggestions={[]}
          appTitle="Topic Hub"
          attachedPreset=""
          onAttachPreset={() => {}}
          uploadDir="uploads"
          chatSwitcher="always"
          showCollections={false}
        />,
        client,
      );
      await waitFor(() => expect(itemChatApi.listChats).toHaveBeenCalled());
      await waitFor(() => expect(screen.getByTestId("chat-switcher-trigger")).toHaveTextContent("B"));
    } finally {
      window.history.pushState({}, "", "/");
    }
  });

  it("finds the chat the address names even in a list cached seconds ago (production staleTime)", async () => {
    // Review round 2: with the app's 30s staleTime a list cached moments ago is
    // FRESH — no refetch on mount, `isFetching` never true — so the hold above
    // never engaged: Run now on /schedules made the chat, the item's list was
    // cached just before, and the last-run link opened the newest chat.
    const { makeQueryClient } = await import("../api/queryClient");
    const a = summary({ chat_id: "conversation:c1", is_default: true, title: "A" });
    const b = summary({ chat_id: "wui:it:sched", is_default: false, title: "B" });
    stubChatApi([a, b]);
    const client = makeQueryClient();
    client.setQueryData(qk.itemChats("topic-hub", "it"), [a]); // cached just now
    window.history.pushState({}, "", "/a/topic-hub/it?chat=wui%3Ait%3Asched");
    try {
      renderWithQuery(
        <ItemChatShell
          slug="topic-hub"
          itemId="it"
              picker={[]}
          suggestions={[]}
          appTitle="Topic Hub"
          attachedPreset=""
          onAttachPreset={() => {}}
          uploadDir="uploads"
          chatSwitcher="always"
          showCollections={false}
        />,
        client,
      );
      await waitFor(() => expect(screen.getByTestId("chat-switcher-trigger")).toHaveTextContent("B"));
    } finally {
      window.history.pushState({}, "", "/");
    }
  });

  it("a ?chat= read for one item holds nothing on the next item the shell shows", async () => {
    // Review round 3: the shell is not remounted between items, so the chat the
    // address named for item A was still held when it showed item B — whose
    // fresh cached list starts no fetch — and B opened with no chat at all.
    const { makeQueryClient } = await import("../api/queryClient");
    const lists: Record<string, ItemChatSummary[]> = {
      A: [
        summary({ chat_id: "conversation:a0", is_default: true, title: "A0" }),
        summary({ chat_id: "wui:A:sched", is_default: false, title: "A-sched" }),
      ],
      B: [summary({ chat_id: "conversation:b0", is_default: true, title: "B0" })],
    };
    stubChatApi([]);
    vi.spyOn(itemChatApi, "listChats").mockImplementation(async (_slug, itemId) => lists[itemId]);
    const client = makeQueryClient();
    client.setQueryData(qk.itemChats("topic-hub", "B"), lists.B); // fresh in the cache
    const shell = (itemId: string) => (
      <ItemChatShell
        slug="topic-hub"
        itemId={itemId}
          picker={[]}
        suggestions={[]}
        appTitle="Topic Hub"
        attachedPreset=""
        onAttachPreset={() => {}}
        uploadDir="uploads"
        chatSwitcher="always"
        showCollections={false}
      />
    );
    window.history.pushState({}, "", "/a/topic-hub/A?chat=wui%3AA%3Asched");
    try {
      const { rerender } = renderWithQuery(shell("A"), client);
      await waitFor(() => expect(screen.getByTestId("chat-switcher-trigger")).toHaveTextContent("A-sched"));

      window.history.pushState({}, "", "/a/topic-hub/B");
      rerender(
        <QueryClientProvider client={client}>
          <DialogProvider>{shell("B")}</DialogProvider>
        </QueryClientProvider>,
      );

      await waitFor(() => expect(screen.getByTestId("chat-switcher-trigger")).toHaveTextContent("B0"));
    } finally {
      window.history.pushState({}, "", "/");
    }
  });

});
