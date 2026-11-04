// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "../api/http";
import type { Opened } from "../api/publish";
import { renderWithQuery } from "../test/queryWrapper";
import { copyRoute, OpenPublishedPage } from "./OpenPublishedPage";

function Where() {
  const loc = useLocation();
  return <div data-testid="landed">{loc.pathname}</div>;
}

function at(address: string, open: (itemId: string, path: string) => Promise<Opened>) {
  const client = { open: vi.fn(open) };
  renderWithQuery(
    <MemoryRouter initialEntries={[address]}>
      <Routes>
        <Route path="/open/:slug/:itemId/*" element={<OpenPublishedPage client={client} />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return client;
}

afterEach(() => cleanup());

describe("OpenPublishedPage", () => {
  it("says it is preparing the copy while the open is in flight", () => {
    at("/open/rca/src-1/pages/a/page.ai.yaml", () => new Promise(() => {}));

    expect(screen.getByRole("status")).toHaveTextContent("正在準備你的副本…");
  });

  it("asks for the opener's copy of the published path, then replaces itself with the copy's WUI", async () => {
    const client = at("/open/rca/src-1/報表/page.ai.yaml", async () => ({
      slug: "rca",
      item_id: "rca:copy-9",
      created: true,
      kind: "wui",
    }));

    expect(await screen.findByTestId("landed")).toHaveTextContent(
      "/w/rca/rca%3Acopy-9/%E5%A0%B1%E8%A1%A8/page.ai.yaml",
    );
    expect(client.open).toHaveBeenCalledTimes(1);
    expect(client.open).toHaveBeenCalledWith("src-1", "/報表/page.ai.yaml");
  });

  it("lands an agent's copy on its conversation", async () => {
    at("/open/rca/src-1/agents/sales/agent.ai.yaml", async () => ({
      slug: "rca",
      item_id: "rca:copy-3",
      created: true,
      kind: "agent",
    }));

    expect(await screen.findByTestId("landed")).toHaveTextContent("/a/rca/rca%3Acopy-3");
  });

  it("says plainly when it is not published or not the opener's to use", async () => {
    at("/open/rca/src-1/pages/a/page.ai.yaml", async () => {
      throw new HttpError(404, "not found");
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("找不到這個發布，或你沒有權限使用它。");
  });

  it("says the source is gone when its workspace was deleted", async () => {
    at("/open/rca/src-1/pages/a/page.ai.yaml", async () => {
      throw new HttpError(410, "gone");
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("發布它的 workspace 已經不在了。");
  });

  it("names any other failure", async () => {
    at("/open/rca/src-1/pages/a/page.ai.yaml", async () => {
      throw new HttpError(500, "disk full");
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("沒辦法打開：disk full");
  });
});

describe("copyRoute", () => {
  it("is the copy's WUI page, or the agent copy's item", () => {
    expect(copyRoute("wui", "rca", "i 1", "/a b/page.ai.yaml")).toBe("/w/rca/i%201/a%20b/page.ai.yaml");
    expect(copyRoute("agent", "rca", "i 1", "/x/agent.ai.yaml")).toBe("/a/rca/i%201");
  });
});
