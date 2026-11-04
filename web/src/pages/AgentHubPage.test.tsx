// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  api: {
    listApps: vi.fn(async () => [{ slug: "rca", title: "根因分析", description: "", icon: "flame", color: "#F0502E" }]),
    getUsers: vi.fn(async () => [{ id: "bob", name: "Bob Lin", section: "", email: "", photo_url: null }]),
    deleteAppItem: vi.fn(async () => {}),
  },
}));
vi.mock("../hooks/useCurrentUser", () => ({
  useCurrentUser: () => "amy",
  useCurrentUserState: () => ({ id: "amy", ready: true }),
}));

import type { AgentCard, AgentPageData } from "../api/agentHub";
import { HttpError } from "../api/http";
import { QueryWrap } from "../test/queryWrapper";
import { AgentHubAgentPage } from "./AgentHubAgentPage";
import { AgentHubPage } from "./AgentHubPage";

const card = (over: Partial<AgentCard> = {}): AgentCard => ({
  id: "rca-investigation%3Asrc∕agents∕sales∕agent.ai.yaml",
  slug: "rca",
  item_id: "rca-investigation:src",
  path: "/agents/sales/agent.ai.yaml",
  title: "Sales helper",
  description: "Order questions.",
  icon: "",
  color: "",
  published_by: "bob",
  published_at: Date.now() - 3_600_000,
  opens: 7,
  people: 3,
  can_remove: false,
  ...over,
});

function Where() {
  const loc = useLocation();
  return <div data-testid="landed">{loc.pathname}</div>;
}

function renderHub(cards: AgentCard[]) {
  const client = { list: vi.fn(async () => cards) };
  render(
    <MemoryRouter>
      <QueryWrap>
        <AgentHubPage client={client} />
      </QueryWrap>
    </MemoryRouter>,
  );
  return client;
}

afterEach(() => cleanup());
beforeEach(() => localStorage.clear());

describe("AgentHubPage", () => {
  it("lists each agent as a card that opens its page, with usage and no names", async () => {
    renderHub([card(), card({ id: "b", title: "Quality helper", opens: 0, people: 0 })]);

    const link = await screen.findByRole("link", { name: "Sales helper" });
    expect(link).toHaveAttribute("href", `/agents/${encodeURIComponent(card().id)}`);
    expect(screen.getAllByTestId("agent-usage")[0]).toHaveTextContent("7 次打開 · 3 人");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Agent Hub");
  });

  it("a starred agent is also listed under favourites, and the star is the viewer's", async () => {
    renderHub([card()]);
    fireEvent.click(await screen.findByRole("button", { name: /把「Sales helper」加入我的最愛/ }));

    const fav = await screen.findByRole("region", { name: "我的最愛" });
    expect(within(fav).getByRole("link", { name: "Sales helper" })).toBeInTheDocument();
    // Agent Hub's stars are its own, not the WUI overview's.
    expect(localStorage.getItem("rca.agentHubFavourites")).toContain(card().item_id);
    expect(localStorage.getItem("rca.wuiFavourites")).toBeNull();

    // Read back from its own store on the next visit.
    cleanup();
    renderHub([card()]);
    expect(await screen.findByRole("region", { name: "我的最愛" })).toBeInTheDocument();
  });

  it("switches to the list and remembers it apart from the WUI overview", async () => {
    renderHub([card()]);
    fireEvent.click(await screen.findByRole("switch"));

    await waitFor(() => expect(document.querySelector(".wui-list")).not.toBeNull());
    expect(localStorage.getItem("rca.agentHubView")).toBe("table");
    expect(localStorage.getItem("rca.wuiView")).toBeNull();
  });

  it("searches titles and descriptions", async () => {
    renderHub([card(), card({ id: "b", title: "Quality helper", description: "Defects." })]);
    await screen.findByRole("link", { name: "Sales helper" });

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "defect" } });

    expect(screen.queryByRole("link", { name: "Sales helper" })).toBeNull();
    expect(screen.getByRole("link", { name: "Quality helper" })).toBeInTheDocument();
  });

  it("says what an agent is when none is published to the viewer", async () => {
    renderHub([]);

    expect(await screen.findByText("還沒有發布給你的 agent。")).toBeInTheDocument();
    expect(screen.queryByRole("switch")).toBeNull();
  });
});

const PAGE: AgentPageData = {
  agent: card(),
  notes: [{ at: Date.now() - 1000, by: "bob", text: "Shorter answers." }],
  mine: [
    { slug: "rca", item_id: "rca-investigation:c2", hint: "Which orders shipped late?", created_at: Date.now() - 1000, bytes: 2048 },
    { slug: "rca", item_id: "rca-investigation:c1", hint: "", created_at: Date.now() - 9000, bytes: 0 },
  ],
};

function renderAgent(page: () => Promise<AgentPageData>) {
  const client = { page: vi.fn(page) };
  const removeItem = vi.fn(async () => {});
  render(
    <MemoryRouter initialEntries={[`/agents/${encodeURIComponent(card().id)}`]}>
      <QueryWrap>
        <Routes>
          <Route path="/agents/:agentId" element={<AgentHubAgentPage client={client} removeItem={removeItem} />} />
          <Route path="*" element={<Where />} />
        </Routes>
      </QueryWrap>
    </MemoryRouter>,
  );
  return { client, removeItem };
}

describe("AgentHubAgentPage", () => {
  it("shows what it is, its notes and the viewer's conversations with their size", async () => {
    const { client } = renderAgent(async () => PAGE);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Sales helper");
    expect(client.page).toHaveBeenCalledWith(card().id);
    expect(screen.getByText("Shorter answers.")).toBeInTheDocument();
    const mine = screen.getByRole("region", { name: "我和它的對話" });
    expect(within(mine).getByRole("link", { name: "Which orders shipped late?" })).toHaveAttribute(
      "href",
      "/a/rca/rca-investigation%3Ac2",
    );
    expect(within(mine).getByRole("link", { name: "（還沒問任何事）" })).toBeInTheDocument();
    expect(mine).toHaveTextContent("2.0 KB");
  });

  it("starts a new conversation through the preparing page", async () => {
    renderAgent(async () => PAGE);

    fireEvent.click(await screen.findByRole("button", { name: "開始新對話" }));

    expect(await screen.findByTestId("landed")).toHaveTextContent("/open/rca/rca-investigation%3Asrc/agents/sales/agent.ai.yaml");
  });

  it("deletes only the picked conversations, after asking", async () => {
    const { removeItem } = renderAgent(async () => PAGE);
    const mine = await screen.findByRole("region", { name: "我和它的對話" });
    const del = within(mine).getByRole("button", { name: "刪除選取的對話" });
    expect(del).toBeDisabled();

    fireEvent.click(within(mine).getByRole("checkbox", { name: "選取「（還沒問任何事）」" }));
    fireEvent.click(del);
    const ask = await screen.findByRole("dialog");
    expect(ask).toHaveTextContent("刪除 1 個對話？");
    expect(removeItem).not.toHaveBeenCalled();
    fireEvent.click(within(ask).getByRole("button", { name: "刪除選取的對話" }));

    await waitFor(() => expect(removeItem).toHaveBeenCalledWith("rca", "rca-investigation:c1"));
    expect(removeItem).toHaveBeenCalledTimes(1);
  });

  it("says plainly when the agent is not there for the viewer", async () => {
    renderAgent(async () => {
      throw new HttpError(404, "no such agent");
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("找不到這個 agent，或它沒有發布給你。");
    expect(screen.getByRole("link", { name: "回到 Agent Hub" })).toHaveAttribute("href", "/agents");
  });
});
