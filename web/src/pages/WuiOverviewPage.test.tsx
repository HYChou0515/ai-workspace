// @vitest-environment happy-dom
/**
 * The WUI overview (`docs/plan-wui-overview.md`): every Deployed page the
 * viewer may open, grouped by app, newest first; Remove where they may.
 */
import "@testing-library/jest-dom/vitest";
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DeployedWui, WuiApi } from "../api/wui";

vi.mock("../api", () => ({
  api: {
    // Two Apps, so a row can be shown under ITS app rather than the first.
    listApps: vi.fn(async () => [
      { slug: "rca", title: "根因分析", description: "", icon: "flame", color: "#F0502E" },
      { slug: "pm", title: "專案管理", description: "", icon: "kanban", color: "#3B82F6" },
    ]),
  },
}));

import { makeQueryClient } from "../api/queryClient";
import { DialogProvider } from "../components/Dialog";
import { translate } from "../lib/i18n";
import { currentWriteFailure, resetWriteFailures } from "../lib/writeFailures";
import { QueryWrap } from "../test/queryWrapper";
import { WuiOverviewPage } from "./WuiOverviewPage";

const row = (over: Partial<DeployedWui>): DeployedWui => ({
  slug: "rca",
  item_id: "i-1",
  item_title: "Line 3 stoppage",
  path: "/pages/report/page.ai.yaml",
  title: "Shipping board",
  deployed_by: "bob",
  deployed_at: 1_700_000_000_000,
  can_remove: true,
  ...over,
});

/** Three pages across two apps, in the order the server sends them (newest
 * Deploy first). */
const THREE: DeployedWui[] = [
  row({ title: "Shipping board", item_id: "i-1", deployed_at: 3 }),
  row({ slug: "pm", item_id: "p-1", item_title: "Q4 roadmap", title: "Burn-down", deployed_at: 2 }),
  row({ title: "Scrap trend", item_id: "i-2", item_title: "Line 4", deployed_at: 1, can_remove: false }),
];

function client(pages: DeployedWui[] = THREE, over: Partial<WuiApi> = {}): WuiApi {
  let current = pages;
  return {
    deploy: vi.fn(async () => pages[0]),
    remove: vi.fn(async (_slug: string, itemId: string, path: string) => {
      current = current.filter((p) => !(p.item_id === itemId && p.path === path));
    }),
    list: vi.fn(async () => current),
    ...over,
  };
}

function Wrap({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter>
      <QueryWrap>{children}</QueryWrap>
    </MemoryRouter>
  );
}

afterEach(cleanup);

/** The product's own words. No `LocaleProvider` is mounted here, so `useT`
 * resolves the context DEFAULT (zh-TW) whatever the runner's `navigator`
 * says — asking `translate` for the same locale keeps the assertion about
 * the words and not about the machine (`reference_ci_node_locale_differs`). */
const word = (key: Parameters<typeof translate>[1]) => translate("zh-TW", key);
const REMOVE = () => new RegExp(`^${word("wui.remove")}`);

describe("WuiOverviewPage", () => {
  it("groups the pages under their app, newest Deploy first within a group", async () => {
    render(<WuiOverviewPage client={client()} />, { wrapper: Wrap });

    const rca = await screen.findByRole("region", { name: "根因分析" });
    const pm = screen.getByRole("region", { name: "專案管理" });
    // Heading text, not hrefs: which heading a row sits under is the claim.
    expect(within(rca).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      expect.stringContaining("Shipping board"),
      expect.stringContaining("Scrap trend"),
    ]);
    expect(within(pm).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      expect.stringContaining("Burn-down"),
    ]);
    // A row says which item it came from and who put it up.
    expect(within(rca).getAllByRole("listitem")[0]).toHaveTextContent("Line 3 stoppage");
    expect(within(rca).getAllByRole("listitem")[0]).toHaveTextContent("bob");
  });

  it("links the title to the page's own address, spelled the way the pane spells it", async () => {
    render(
      <WuiOverviewPage
        client={client([row({ path: "/報表/出貨 看板/page.ai.yaml", title: "出貨看板" })])}
      />,
      { wrapper: Wrap },
    );

    const link = await screen.findByRole("link", { name: "出貨看板" });
    // `WuiView.tsx`'s `address`: origin-relative here, the base then the
    // route, the path encoded segment by segment so a CJK folder with a
    // space round-trips through the router.
    expect(link).toHaveAttribute(
      "href",
      "/w/rca/i-1/%E5%A0%B1%E8%A1%A8/%E5%87%BA%E8%B2%A8%20%E7%9C%8B%E6%9D%BF/page.ai.yaml",
    );
    // The reader page has no way back (it renders outside the shell), so it
    // opens beside the overview rather than replacing it.
    expect(link).toHaveAttribute("target", "_blank");
    // The item link opens the workspace — the item, since the workspace has no
    // deep link to a file.
    expect(screen.getByRole("link", { name: "Line 3 stoppage" })).toHaveAttribute(
      "href",
      "/a/rca/i-1",
    );
  });

  it("draws Remove only where the server said this viewer may", async () => {
    render(<WuiOverviewPage client={client()} />, { wrapper: Wrap });
    const rca = await screen.findByRole("region", { name: "根因分析" });

    const [mine, theirs] = within(rca).getAllByRole("listitem");
    expect(within(mine).getByRole("button", { name: REMOVE() })).toBeInTheDocument();
    expect(within(theirs).queryByRole("button", { name: REMOVE() })).toBeNull();
  });

  it("asks once before removing, names the page, and the row is gone after", async () => {
    const c = client();
    render(<WuiOverviewPage client={c} />, { wrapper: Wrap });
    const rca = await screen.findByRole("region", { name: "根因分析" });
    fireEvent.click(within(rca).getAllByRole("button", { name: REMOVE() })[0]);

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Shipping board");
    expect(c.remove).not.toHaveBeenCalled(); // asked, not done
    fireEvent.click(within(dialog).getByRole("button", { name: word("wui.remove") }));

    await waitFor(() =>
      expect(c.remove).toHaveBeenCalledWith("rca", "i-1", "/pages/report/page.ai.yaml"),
    );
    await waitFor(() => expect(screen.queryByText("Shipping board")).toBeNull());
    expect(screen.getByText("Scrap trend")).toBeInTheDocument();
  });

  it("removes nothing when the question is answered Cancel", async () => {
    const c = client();
    render(<WuiOverviewPage client={c} />, { wrapper: Wrap });
    const rca = await screen.findByRole("region", { name: "根因分析" });
    fireEvent.click(within(rca).getAllByRole("button", { name: REMOVE() })[0]);

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: word("wui.remove.cancel") }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(c.remove).not.toHaveBeenCalled();
    expect(screen.getByText("Shipping board")).toBeInTheDocument();
  });

  it("says the listing could not be read, and reads it again on request", async () => {
    // Review round 1: `isLoading || !data` rendered "載入中…" forever after a
    // rejected list — no sentence, no retry, indistinguishable from a slow
    // read. Compare `MyResourcesPage`, which still has that shape.
    let fail = true;
    const c = client(THREE, {
      list: vi.fn(async () => {
        if (fail) throw new Error("boom");
        return THREE;
      }),
    });
    render(<WuiOverviewPage client={c} />, { wrapper: Wrap });

    const said = await screen.findByRole("alert");
    expect(said).toHaveTextContent(word("wui.error"));
    expect(screen.queryByText(word("wui.loading"))).toBeNull();
    fail = false;
    fireEvent.click(within(said).getByRole("button", { name: word("wui.retry") }));

    expect(await screen.findByText("Shipping board")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("reports a failed Remove in its row only — not as the app-wide write-failure notice too", async () => {
    // Under the REAL query client: its mutation cache routes every rejected
    // mutation to the global notice unless the mutation says it renders its
    // own — `LiveEnvironmentRow` opts out for exactly this reason, and the
    // first version here copied the row without the opt-out (review round 1).
    resetWriteFailures();
    const c = client(THREE, { remove: vi.fn(async () => { throw new Error("remove boom"); }) });
    render(
      <MemoryRouter>
        <QueryClientProvider client={makeQueryClient()}>
          <DialogProvider>
            <WuiOverviewPage client={c} />
          </DialogProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );
    const rca = await screen.findByRole("region", { name: "根因分析" });
    fireEvent.click(within(rca).getAllByRole("button", { name: REMOVE() })[0]);
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: word("wui.remove") }));

    const said = await within(rca).findByRole("alert");
    expect(said).toHaveTextContent(word("wui.remove.failed"));
    expect(currentWriteFailure()).toBeNull();
    // The row stays, and so does its button — there is something to press again.
    expect(within(rca).getAllByRole("button", { name: REMOVE() })[0]).toBeEnabled();
  });

  it("says what a WUI is when there is nothing to list, and where to read more", async () => {
    render(<WuiOverviewPage client={client([])} />, { wrapper: Wrap });

    expect(await screen.findByText(word("wui.empty"))).toBeInTheDocument();
    expect(screen.getByRole("link", { name: word("wui.empty.help") })).toHaveAttribute("href", "/help");
    expect(screen.queryByRole("region")).toBeNull();
  });
});
