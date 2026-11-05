// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentPageData } from "../api/agentHub";
import { HttpError } from "../api/http";
import { renderWithQuery } from "../test/queryWrapper";
import { AgentLaunchPage } from "./AgentLaunchPage";

const ID = "rca-investigation%3Asrc∕agents∕sales∕agent.ai.yaml";
const agent = {
  id: ID,
  slug: "rca",
  item_id: "rca-investigation:src",
  path: "/agents/sales/agent.ai.yaml",
  title: "Sales helper",
  description: "",
  icon: "",
  color: "",
  published_by: "bob",
  published_at: 1,
  opens: 0,
  people: 0,
  can_remove: false,
};
const conv = (item_id: string, created_at: number) => ({ slug: "rca", item_id, hint: "", created_at, bytes: 0 });

function Where() {
  const loc = useLocation();
  return <div data-testid="landed">{loc.pathname}</div>;
}

function go(address: string, page: () => Promise<AgentPageData>) {
  const client = { page: vi.fn(page) };
  renderWithQuery(
    <MemoryRouter initialEntries={[address]}>
      <Routes>
        <Route path="/agents/:agentId/go" element={<AgentLaunchPage client={client} />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return client;
}

afterEach(() => cleanup());

// The user's decision (2026-11-05, overturning A8's "land on the agent's
// page"): clicking an agent goes straight into a conversation — NN/g,
// *interaction cost*: what people come for is to talk, not to choose first.
describe("AgentLaunchPage", () => {
  it("goes back to the person's latest conversation with the agent", async () => {
    // `mine` arrives newest first (`api/agent_hub_routes.py`).
    go(`/agents/${encodeURIComponent(ID)}/go`, async () => ({
      agent,
      notes: [],
      mine: [conv("rca-investigation:c2", 2), conv("rca-investigation:c1", 1)],
    }));

    expect(await screen.findByTestId("landed")).toHaveTextContent("/a/rca/rca-investigation%3Ac2");
  });

  it("opens a first one when there is none — never an empty page to start from", async () => {
    go(`/agents/${encodeURIComponent(ID)}/go`, async () => ({ agent, notes: [], mine: [] }));

    expect(await screen.findByTestId("landed")).toHaveTextContent(
      "/open/rca/rca-investigation%3Asrc/agents/sales/agent.ai.yaml",
    );
  });

  it("opens a new one when asked for a new conversation, whatever came before", async () => {
    go(`/agents/${encodeURIComponent(ID)}/go?new=1`, async () => ({
      agent,
      notes: [],
      mine: [conv("rca-investigation:c2", 2)],
    }));

    expect(await screen.findByTestId("landed")).toHaveTextContent(
      "/open/rca/rca-investigation%3Asrc/agents/sales/agent.ai.yaml",
    );
  });

  it("says plainly when the agent is not there for the viewer", async () => {
    go(`/agents/${encodeURIComponent(ID)}/go`, async () => {
      throw new HttpError(404, "no such agent");
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("找不到這個 agent，或它沒有發布給你。");
  });
});
