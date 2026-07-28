// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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
    { id: "e:1", name: "回焊爐", kind: "機台", aliases: ["reflow oven"], collection_ids: ["c1"] },
    { id: "e:2", name: "PPOOIXUX", kind: "recipe", aliases: [], collection_ids: [] },
  ],
  has_more: true,
  next_offset: 2,
};
const PAGE2 = {
  items: [{ id: "e:3", name: "錫膏", kind: "材料", aliases: [], collection_ids: ["c1"] }],
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

const COLLECTIONS = JSON.stringify([
  { resource_id: "c1", name: "製程週報" },
  { resource_id: "c2", name: "良率檢討" },
]);

/** Like `stub`, but the collections endpoint answers with real rows so the
 *  "found in" column has names to resolve against. */
function stubWithCollections(page: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (String(url).includes("/kb/collections")) {
        return new Response(COLLECTIONS, { status: 200 });
      }
      return new Response(JSON.stringify(page), { status: 200 });
    }),
  );
}

describe("GraphBrowsePage — which collections vouch for a row", () => {
  it("names the collections the reader can open", async () => {
    stubWithCollections({
      items: [{ id: "e:1", name: "回焊爐", kind: "機台", aliases: [], collection_ids: ["c1"] }],
      has_more: false,
      next_offset: 1,
    });
    show();
    const list = await screen.findByTestId("graph-browse-list");
    expect(within(list).getByText("製程週報")).toBeInTheDocument();
  });

  it("counts the ones it cannot, instead of dropping them silently", async () => {
    // An identity is visible when ANY of its collections is readable, so a row
    // can carry evidence from one this reader may not open. Saying "+1" tells
    // them the thing is broader than what they see; hiding it would misreport
    // the corpus as smaller than it is.
    stubWithCollections({
      items: [
        {
          id: "e:1",
          name: "回焊爐",
          kind: "機台",
          aliases: [],
          collection_ids: ["c1", "locked-1", "locked-2"],
        },
      ],
      has_more: false,
      next_offset: 1,
    });
    show();
    const list = await screen.findByTestId("graph-browse-list");
    expect(within(list).getByText("製程週報")).toBeInTheDocument();
    expect(within(list).getByText(/\+2/)).toBeInTheDocument();
  });

  it("says nothing when the row names no collection it can resolve", async () => {
    stubWithCollections({
      items: [{ id: "e:1", name: "回焊爐", kind: "機台", aliases: [], collection_ids: [] }],
      has_more: false,
      next_offset: 1,
    });
    show();
    const list = await screen.findByTestId("graph-browse-list");
    expect(within(list).getByText("回焊爐")).toBeInTheDocument();
    expect(within(list).queryByText(/\+\d/)).not.toBeInTheDocument();
  });
});

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
