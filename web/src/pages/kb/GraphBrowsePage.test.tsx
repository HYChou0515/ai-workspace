// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../../test/queryWrapper";
import { GraphBrowsePage } from "./GraphBrowsePage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PAGE1 = {
  items: [
    { id: "e:1", name: "回焊爐", kind: "機台", aliases: ["reflow oven"] },
    { id: "e:2", name: "PPOOIXUX", kind: "recipe", aliases: [] },
  ],
  has_more: true,
  next_offset: 2,
};
const PAGE2 = {
  items: [{ id: "e:3", name: "錫膏", kind: "材料", aliases: [] }],
  has_more: false,
  next_offset: 4,
};

function stub(byUrl: (url: string) => unknown) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(String(url));
      // the collections picker calls its own endpoint, which returns a list
      if (String(url).includes("/collection")) {
        return new Response("[]", { status: 200 });
      }
      return new Response(JSON.stringify(byUrl(String(url))), { status: 200 });
    }),
  );
  return calls;
}

const show = () =>
  render(
    <MemoryRouter>
      <GraphBrowsePage />
    </MemoryRouter>,
    { wrapper: QueryWrap },
  );

describe("GraphBrowsePage (#636)", () => {
  it("lists what the graph built, each row opening its page", async () => {
    stub(() => PAGE1);
    show();
    expect(await screen.findByText("回焊爐")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /回焊爐/ })).toHaveAttribute(
      "href",
      "/kb/graph/entities/e:1",
    );
    expect(screen.getByText("PPOOIXUX")).toBeInTheDocument();
  });

  it("pages forward without ever claiming a total", async () => {
    const calls = stub((url) => (url.includes("offset=2") ? PAGE2 : PAGE1));
    show();
    await screen.findByText("回焊爐");
    // no total is rendered anywhere — the API cannot produce one cheaply
    expect(screen.queryByText(/共 \d+ 頁|of \d+ pages/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /下一頁|next/i }));
    await screen.findByText("錫膏");
    expect(calls.some((c) => c.includes("offset=2"))).toBe(true);
  });

  it("hides the next-page control on the last page", async () => {
    stub(() => PAGE2);
    show();
    await screen.findByText("錫膏");
    expect(screen.queryByRole("button", { name: /下一頁|next/i })).not.toBeInTheDocument();
  });

  it("searches by name and starts over at the first page", async () => {
    const calls = stub((url) => (url.includes("q=") ? PAGE2 : PAGE1));
    show();
    await screen.findByText("回焊爐");
    await userEvent.type(screen.getByRole("searchbox"), "錫");
    await waitFor(() => expect(calls.some((c) => c.includes("q=%E9%8C%AB"))).toBe(true));
    await waitFor(() => expect(calls.some((c) => c.includes("offset=0"))).toBe(true));
  });

  it("says so plainly when there is nothing", async () => {
    stub(() => ({ items: [], has_more: false, next_offset: 0 }));
    show();
    expect(await screen.findByTestId("graph-browse-empty")).toBeInTheDocument();
  });
});
