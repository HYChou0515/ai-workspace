// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import type { AppItem } from "../api/types";
import { AgentCopyActions, AgentCopyBack, isAgentCopy } from "./AgentCopyControls";

const item = (over: Partial<AppItem>): AppItem => ({
  resource_id: "rca-investigation:c1",
  title: "Sales helper",
  owner: "amy",
  created_time: "",
  created_by: "amy",
  ...over,
});

afterEach(() => cleanup());

// The user's decision (2026-11-05): a new conversation and the history are a
// press away inside a conversation — and the way back goes to Agent Hub.
describe("AgentCopyControls", () => {
  it("goes back to Agent Hub, and offers a new conversation and the history", () => {
    const from = "rca-investigation%3Asrc∕a∕agent.ai.yaml";
    render(
      <MemoryRouter>
        <AgentCopyBack />
        <AgentCopyActions item={item({ published_from: from })} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: /回到 Agent Hub/ })).toHaveAttribute("href", "/agents");
    expect(screen.getByRole("link", { name: "新對話" })).toHaveAttribute(
      "href",
      `/agents/${encodeURIComponent(from)}/go?new=1`,
    );
    expect(screen.getByRole("link", { name: "歷史對話" })).toHaveAttribute("href", `/agents/${encodeURIComponent(from)}`);
  });

  it("offers nothing to go to when the copy does not say where it came from", () => {
    render(
      <MemoryRouter>
        <AgentCopyActions item={item({})} />
      </MemoryRouter>,
    );

    expect(screen.queryByRole("link")).toBeNull();
  });

  it("knows a conversation copy from anything else", () => {
    expect(isAgentCopy(item({ published_kind: "agent" }))).toBe(true);
    expect(isAgentCopy(item({ published_kind: "wui" }))).toBe(false);
    expect(isAgentCopy(item({}))).toBe(false);
  });
});
