// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  api: {
    getUsers: vi.fn(async () => [{ id: "bob", name: "Bob Lin", section: "", email: "", photo_url: null }]),
  },
}));

import type { AgentSummary } from "../../api/agents";
import { FileServiceProvider, type FileService } from "../../api/fileService";
import type { Published } from "../../api/publish";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { publishAgentTestChat, subscribeAgentTestChat } from "../../lib/agentTestChatBus";
import { QueryWrap } from "../../test/queryWrapper";
import { AgentYamlPanel } from "./AgentYamlPanel";

const PATH = "/agents/sales/agent.ai.yaml";
const YAML = "view: agent\ntitle: Sales helper\ninstructions: Help.\npublish:\n  reach: everyone\n";

function fs(): FileService {
  return {
    scopeId: "item1",
    caps: { write: true, delete: true },
    readFile: vi.fn(async (path: string) => ({
      kind: "text",
      path,
      size: YAML.length,
      text: YAML,
      encoding: "utf-8",
    })),
    writeFile: vi.fn(async () => {}),
    fileDownloadUrl: (p: string) => `/api/files${p}`,
  } as unknown as FileService;
}

const SUMMARY: AgentSummary = {
  path: PATH,
  valid: true,
  problems: [],
  title: "Sales helper",
  description: "Order questions.",
  icon: "",
  color: "",
  instructions: "Help.",
  narrows: true,
  tools: ["read_file", "list_files"],
  not_available: ["kb_search"],
  skills: ["grill", "quote"],
  suggestions: [{ label: "Late orders", prompt: "Late orders" }],
  published: null,
  opens: 0,
  people: 0,
  notes: [],
};

const ROW: Published = {
  id: "r",
  kind: "agent",
  slug: "rca",
  item_id: "item1",
  path: PATH,
  title: "Sales helper",
  sharing: "per_conversation",
  reach: "subjects",
  subjects: ["user:amy"],
  role: "participant",
  published_by: "bob",
  published_at: Date.now() - 60_000,
  icon: "",
  color: "",
  description: "",
  files: 3,
  bytes: 100,
  based_on_title: "",
  based_on_by: "",
  cpu_cores: 2,
  memory_bytes: null,
};

function renderPanel(summary: Partial<AgentSummary> = {}) {
  const client = { summary: vi.fn(async () => ({ ...SUMMARY, ...summary })) };
  const publisher = {
    publish: vi.fn(async () => ROW),
    unpublish: vi.fn(async () => {}),
    estimate: vi.fn(async () => ({ files: 3, bytes: 100, biggest: [] })),
  };
  render(
    <QueryWrap>
      <WorkspaceSlugProvider value="rca">
        <FileServiceProvider value={fs()}>
          <AgentYamlPanel path={PATH} text={YAML} client={client} publisher={publisher} />
        </FileServiceProvider>
      </WorkspaceSlugProvider>
    </QueryWrap>,
  );
  return { client, publisher };
}

afterEach(() => cleanup());

describe("AgentYamlPanel", () => {
  it("shows what a conversation with it will hold before it is published", async () => {
    const { client } = renderPanel();
    const panel = await screen.findByTestId("agent-panel");

    expect(within(panel).getByRole("heading", { name: "Sales helper" })).toBeInTheDocument();
    expect(panel).toHaveTextContent("Order questions.");
    expect(within(panel).getByTestId("agent-tools")).toHaveTextContent("read_file, list_files");
    expect(within(panel).getByTestId("agent-not-available")).toHaveTextContent("kb_search");
    expect(within(panel).getByTestId("agent-skills")).toHaveTextContent("grill, quote");
    expect(panel).toHaveTextContent("Late orders");
    expect(within(panel).getByTestId("agent-publish-state")).toHaveTextContent("還沒發布。");
    expect(client.summary).toHaveBeenCalledWith("rca", "item1", PATH);
  });

  it("says when it holds every tool the App gives", async () => {
    renderPanel({ narrows: false, not_available: [] });

    expect(await screen.findByTestId("agent-tools")).toHaveTextContent("全部工具");
  });

  it("names what the file gets wrong and will not publish until it is fixed", async () => {
    renderPanel({ problems: ["unknown key `model`"] });
    const panel = await screen.findByTestId("agent-panel");

    expect(within(panel).getByRole("alert")).toHaveTextContent("unknown key `model`");
    expect(within(panel).getByRole("button", { name: "發布 agent" })).toBeDisabled();
  });

  it("a file that is not an agent yet offers neither a summary nor a test", async () => {
    renderPanel({ valid: false, problems: ["`title` is required"], title: "" });
    const panel = await screen.findByTestId("agent-panel");

    expect(within(panel).getByRole("alert")).toHaveTextContent("這個檔案還不是一個 agent");
    expect(within(panel).queryByTestId("agent-tools")).toBeNull();
    expect(within(panel).getByRole("button", { name: "測試對話" })).toBeDisabled();
  });

  it("publishes one conversation per copy, pre-filled from the yaml's publish:", async () => {
    const { publisher } = renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "發布 agent" }));
    const dialog = screen.getByTestId("publish-dialog");

    expect(within(dialog).getByRole("radio", { name: "所有登入的人" })).toBeChecked();
    // A first publish has nothing to say "what changed" about.
    expect(within(dialog).queryByRole("textbox", { name: "這次改了什麼（選填）" })).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "發布" }));

    await waitFor(() =>
      expect(publisher.publish).toHaveBeenCalledWith(
        "rca",
        "item1",
        PATH,
        expect.objectContaining({ sharing: "per_conversation", reach: "everyone" }),
      ),
    );
    expect(await screen.findByText(/已發布。之後每個新對話都會用這一版/)).toBeInTheDocument();
  });

  it("once published, says how, by whom, its usage and notes — and re-publishes with a note", async () => {
    const { publisher } = renderPanel({
      published: ROW,
      opens: 7,
      people: 3,
      notes: [{ at: Date.now() - 1000, by: "bob", text: "Answers in Chinese now." }],
    });
    const state = await screen.findByTestId("agent-publish-state");

    expect(state).toHaveTextContent("指定的人或群組");
    expect(state).toHaveTextContent("7 次打開 · 3 人");
    expect(state).toHaveTextContent("Answers in Chinese now.");
    await waitFor(() => expect(state).toHaveTextContent("Bob Lin"));

    fireEvent.click(screen.getByRole("button", { name: "再次發布 agent" }));
    const dialog = screen.getByTestId("publish-dialog");
    // What is in effect now, not the yaml's first idea — the size too.
    expect(within(dialog).getByRole("radio", { name: "指定的人或群組" })).toBeChecked();
    expect(within(dialog).getByRole("textbox", { name: "CPU 核心數" })).toHaveValue("2");
    // A note says who wrote it (A9) — in the note, not only the line above it.
    const notes = within(state).getByRole("list", { name: "更新說明" });
    expect(notes).toHaveTextContent("Bob Lin");
    fireEvent.change(within(dialog).getByRole("textbox", { name: "這次改了什麼（選填）" }), {
      target: { value: "Shorter answers." },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "發布" }));

    await waitFor(() =>
      expect(publisher.publish).toHaveBeenCalledWith(
        "rca",
        "item1",
        PATH,
        expect.objectContaining({ note: "Shorter answers.", subjects: ["user:amy"] }),
      ),
    );
  });

  it("unpublishes only after asking", async () => {
    const { publisher } = renderPanel({ published: ROW });
    fireEvent.click(await screen.findByRole("button", { name: "停止發布" }));
    const ask = await screen.findByRole("dialog");
    expect(ask).toHaveTextContent("停止發布「Sales helper」？");
    expect(publisher.unpublish).not.toHaveBeenCalled();

    fireEvent.click(within(ask).getByRole("button", { name: "停止發布" }));

    await waitFor(() => expect(publisher.unpublish).toHaveBeenCalledWith("rca", "item1", PATH));
  });

  it("opens a test chat beside the file through the item's chat", async () => {
    const asked = vi.fn();
    const stop = subscribeAgentTestChat("item1", asked);
    try {
      renderPanel();
      fireEvent.click(await screen.findByRole("button", { name: "測試對話" }));

      expect(asked).toHaveBeenCalledWith({ path: PATH, title: "測試：Sales helper" });
    } finally {
      stop();
    }
  });

  it("offers no test chat where no chat is beside it to answer", async () => {
    renderPanel();

    expect(await screen.findByRole("button", { name: "測試對話" })).toBeDisabled();
    // (The bus itself is the one `ItemChatShell` listens on.)
    expect(publishAgentTestChat).toBeTypeOf("function");
  });
});
