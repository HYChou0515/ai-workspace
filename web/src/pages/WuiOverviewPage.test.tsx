// @vitest-environment happy-dom
/**
 * The WUI overview (`docs/plan-wui-overview.md`): every Deployed page the
 * viewer may open, grouped by app, newest first; Remove where they may.
 */
import "@testing-library/jest-dom/vitest";
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

// The signed-in user, and whether the user query has SETTLED: until it has,
// the id is the "default-user" placeholder — an identity, not the identity.
let me = { id: "alice", ready: true };
vi.mock("../hooks/useCurrentUser", () => ({
  useCurrentUser: () => me.id,
  useCurrentUserState: () => me,
}));

import { makeQueryClient } from "../api/queryClient";
import { exactTime, relativeTime } from "../api/types";
import { DialogProvider } from "../components/Dialog";
import { BreadcrumbProvider, useBreadcrumbTrail } from "../hooks/breadcrumbs";
import { translate } from "../lib/i18n";
import { favouriteKey, readFavourites, toggleFavourite } from "../lib/wuiFavourites";
import { readWuiView, writeWuiView } from "../lib/wuiView";
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
  icon: "",
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
beforeEach(() => {
  localStorage.clear();
  me = { id: "alice", ready: true };
  // The tests below this block were written for the table (P1–P17) and
  // stay the table's: cards are the default (the cards amendment), so each
  // run opts back into the table first. The cards suite at the end clears
  // this again.
  writeWuiView("table");
});

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

  it("says when a page was Deployed the way the rest of the shell says when — relative, in a sentence built for it", async () => {
    // Review round 2: the sentence template was written for an absolute date
    // ("{who} 於 {when} Deploy") and P5 dropped `relativeTime` into it —
    // "bob 於 just now Deploy". The template now takes the relative form, and
    // the exact stamp sits in the title the way `GroupsPage` pairs them.
    const at = Date.now() - 2 * 24 * 60 * 60 * 1000;
    render(<WuiOverviewPage client={client([row({ deployed_at: at })])} />, { wrapper: Wrap });

    const rca = await screen.findByRole("region", { name: "根因分析" });
    const iso = new Date(at).toISOString();
    const when = relativeTime(iso); // "2 d ago"
    // The sentence LITERALLY, not `translate(...)` of the same template: an
    // expectation derived from the thing under test pins nothing — round 4
    // reverted the template to "{who} 於 {when} Deploy" and the derived form
    // stayed green. The relative form has to come LAST, where "on 2 d ago"
    // / "於 just now Deploy" cannot be made to read.
    expect(within(rca).getByRole("listitem")).toHaveTextContent(`bob Deploy · ${when}`);
    expect(within(rca).getByTitle(exactTime(iso))).toBeInTheDocument();
  });

  it("draws Remove and Try again as buttons, not as words", async () => {
    // Review round 2: `data-size="sm"` styles nothing without `className="btn"`
    // (base.css resets every button to bare text), so Remove had no border,
    // no height, and `disabled` was invisible — and Try again rendered in the
    // error sentence's red, reading as a word in the sentence.
    const c = client(THREE, { list: vi.fn(async () => { throw new Error("boom"); }) });
    render(<WuiOverviewPage client={c} />, { wrapper: Wrap });
    const retry = within(await screen.findByRole("alert")).getByRole("button", {
      name: word("wui.retry"),
    });
    // Both halves: `.btn` alone has no colour of its own (it inherits — here,
    // the error sentence's red) and a transparent border; the variant is
    // what draws it. Round 4 dropped `data-variant` and the suite stayed green.
    expect(retry).toHaveClass("btn");
    expect(retry).toHaveAttribute("data-variant", "secondary");
    cleanup();

    render(<WuiOverviewPage client={client()} />, { wrapper: Wrap });
    const rca = await screen.findByRole("region", { name: "根因分析" });
    const remove = within(rca).getAllByRole("button", { name: REMOVE() })[0];
    expect(remove).toHaveClass("btn");
    expect(remove).toHaveAttribute("data-variant", "secondary");
    // The word is 下架 — the opposite of Deploy's 上架 — not 移除, which read
    // as "delete the page" (the author: 「移除是什麼意思？」). Literal, and
    // the tooltip says what stays.
    expect(remove).toHaveTextContent("下架");
    expect(remove).toHaveAttribute("title", "從 WUI 總覽下架；頁面和資料夾都留著");
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

  it("leads every row with the page's mark — its icon when it has one, its letters when not", async () => {
    render(
      <WuiOverviewPage
        client={client([
          row({ title: "Shipping board", icon: "📦" }),
          row({ item_id: "i-2", title: "Scrap trend", icon: "" }),
        ])}
      />,
      { wrapper: Wrap },
    );

    const items = within(await screen.findByRole("region", { name: "根因分析" })).getAllByRole(
      "listitem",
    );
    const marks = items.map((li) => within(li).getByTestId("page-mark"));
    expect(marks[0]).toHaveTextContent("📦");
    expect(marks[1]).toHaveTextContent("ST");
    // First in the row, so the grid's leading track is the mark's on every
    // row — the sheet maps `> li > .page-mark` to column 1.
    expect(items[0].firstElementChild).toBe(marks[0]);
    // The mark does not become part of the row's name.
    expect(within(items[0]).getByRole("link", { name: "Shipping board" })).toBeInTheDocument();
  });

  describe("favourites (docs/plan-wui-overview-icon-favourites.md)", () => {
    const FAV = () => word("wui.favourites");
    const STAR = (title: string) => translate("zh-TW", "wui.star", { title });
    const UNSTAR = (title: string) => translate("zh-TW", "wui.unstar", { title });
    const favGroup = () => screen.queryByRole("region", { name: FAV() });

    it("draws no favourites group until something is starred, and a star on every row", async () => {
      render(<WuiOverviewPage client={client()} />, { wrapper: Wrap });
      await screen.findByRole("region", { name: "根因分析" });

      expect(favGroup()).toBeNull();
      const star = screen.getByRole("button", { name: STAR("Shipping board") });
      // The state is `aria-pressed`; the label is the ACTION — it names the
      // page and says which way the press goes. LITERALLY once: `STAR()` is
      // `translate()` of the key the page uses, so a template that lost its
      // `{title}` would lose it on both sides and stay green — with every
      // star on the page then reading the same (round 4's lesson).
      expect(star).toHaveAttribute("aria-label", "把「Shipping board」加入我的最愛");
      expect(star).toHaveAttribute("aria-pressed", "false");
      // `.btn` + a variant, or base.css leaves it bare text (review round 2 /
      // 4 of this PR) — and a read-only viewer gets a star too: starring is
      // theirs, Remove is not.
      expect(star).toHaveClass("btn");
      expect(star).toHaveAttribute("data-variant", "ghost");
      expect(screen.getByRole("button", { name: STAR("Scrap trend") })).toBeInTheDocument();
    });

    it("puts a starred page in a favourites group at the top — and leaves it under its App", async () => {
      render(<WuiOverviewPage client={client()} />, { wrapper: Wrap });
      await screen.findByRole("region", { name: "根因分析" });

      fireEvent.click(screen.getByRole("button", { name: STAR("Burn-down") }));

      const fav = favGroup();
      expect(fav).not.toBeNull();
      // Literally, in the product's words: the heading is what a reader lands
      // on, and a heading derived from the key under test pins nothing.
      expect(within(fav!).getByRole("heading")).toHaveTextContent("我的最愛");
      // First on the page: the shortcut sits above the complete listing.
      const regions = screen.getAllByRole("region");
      expect(regions[0]).toBe(fav);
      expect(within(fav!).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
        expect.stringContaining("Burn-down"),
      ]);
      // …and still under 專案管理, where the complete listing keeps it.
      const pm = screen.getByRole("region", { name: "專案管理" });
      expect(within(pm).getAllByRole("listitem")).toHaveLength(1);
      // Both copies of the row show the same state: two stars, both pressed,
      // both now offering to unstar.
      expect(screen.getAllByRole("button", { name: UNSTAR("Burn-down") })).toHaveLength(2);
      for (const b of screen.getAllByRole("button", { name: UNSTAR("Burn-down") })) {
        expect(b).toHaveAttribute("aria-pressed", "true");
        expect(b).toHaveAttribute("aria-label", "把「Burn-down」從我的最愛移除");
      }
      // Written through, under this user.
      expect(readFavourites("alice")).toEqual([favouriteKey(THREE[1])]);
    });

    it("lists two favourites from two Apps in the listing's order, and drops the group when the last star goes", async () => {
      render(<WuiOverviewPage client={client()} />, { wrapper: Wrap });
      await screen.findByRole("region", { name: "根因分析" });

      // Starred in the OPPOSITE order to the listing: the group follows the
      // listing (newest Deploy first), not the order of starring.
      fireEvent.click(screen.getByRole("button", { name: STAR("Scrap trend") }));
      fireEvent.click(screen.getByRole("button", { name: STAR("Burn-down") }));

      expect(within(favGroup()!).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
        expect.stringContaining("Burn-down"),
        expect.stringContaining("Scrap trend"),
      ]);

      // Unstar from inside the favourites group: its copy and the App group's
      // copy both flip; the group survives while one star remains.
      fireEvent.click(within(favGroup()!).getAllByRole("button", { name: UNSTAR("Burn-down") })[0]);
      expect(within(favGroup()!).getAllByRole("listitem")).toHaveLength(1);
      expect(screen.getByRole("button", { name: STAR("Burn-down") })).toHaveAttribute("aria-pressed", "false");

      fireEvent.click(within(favGroup()!).getByRole("button", { name: UNSTAR("Scrap trend") }));
      expect(favGroup()).toBeNull();
      expect(readFavourites("alice")).toEqual([]);
    });

    it("draws nothing for a starred page the listing no longer returns, and keeps its key", async () => {
      // Starred earlier; since Removed, or its item closed to this viewer.
      toggleFavourite("alice", "gone/pages/old/page.ai.yaml");
      toggleFavourite("alice", favouriteKey(THREE[0]));
      render(<WuiOverviewPage client={client()} />, { wrapper: Wrap });
      await screen.findByRole("region", { name: "根因分析" });

      expect(within(favGroup()!).getAllByRole("listitem")).toHaveLength(1);
      expect(readFavourites("alice")).toEqual(["gone/pages/old/page.ai.yaml", favouriteKey(THREE[0])]);
    });

    it("holds the star until the user query has settled, so a press is never filed under the placeholder", async () => {
      // Code review of P14: a cold deep-link to /wui races the listing against
      // the current-user query; a star pressed in that window was written under
      // "default-user" and silently unfilled when the real id arrived.
      me = { id: "default-user", ready: false };
      render(<WuiOverviewPage client={client()} />, { wrapper: Wrap });
      await screen.findByRole("region", { name: "根因分析" });

      const star = screen.getByRole("button", { name: STAR("Shipping board") });
      expect(star).toBeDisabled();
      fireEvent.click(star);
      expect(localStorage.getItem("rca.wuiFavourites")).toBeNull();
      expect(favGroup()).toBeNull();
    });

    it("stars are the viewer's own: another user's stars do not show", async () => {
      toggleFavourite("bob", favouriteKey(THREE[0]));
      render(<WuiOverviewPage client={client()} />, { wrapper: Wrap });
      await screen.findByRole("region", { name: "根因分析" });
      expect(favGroup()).toBeNull();
    });

    it("Remove pressed in the favourites group takes the row out of both groups with one DELETE", async () => {
      toggleFavourite("alice", favouriteKey(THREE[0]));
      const c = client();
      render(<WuiOverviewPage client={c} />, { wrapper: Wrap });
      await screen.findByRole("region", { name: "根因分析" });
      expect(screen.getAllByRole("link", { name: "Shipping board" })).toHaveLength(2);

      fireEvent.click(within(favGroup()!).getByRole("button", { name: REMOVE() }));
      const dialog = await screen.findByRole("dialog");
      fireEvent.click(within(dialog).getByRole("button", { name: word("wui.remove") }));

      await waitFor(() => expect(screen.queryByRole("link", { name: "Shipping board" })).toBeNull());
      expect(c.remove).toHaveBeenCalledTimes(1);
      expect(favGroup()).toBeNull();
    });
  });

  describe("cards (the cards amendment)", () => {
    const CARDS = () => translate("zh-TW", "wui.view.cards");
    const TABLE = () => translate("zh-TW", "wui.view.table");
    beforeEach(() => localStorage.clear());

    it("draws cards by default, in a wide shell, and no table", async () => {
      const { container } = render(<WuiOverviewPage client={client()} />, { wrapper: Wrap });
      const rca = await screen.findByRole("region", { name: "根因分析" });

      expect(rca.querySelector("ul.wui-cards")).not.toBeNull();
      expect(container.querySelector(".wui-list")).toBeNull();
      // Three cards fit only at the Launcher's width: the shell widens.
      expect(container.querySelector(".page")).toHaveClass("page--wide");
      // The toggle says which is on, LITERALLY (round 4's lesson).
      const cards = screen.getByRole("button", { name: "卡片" });
      const table = screen.getByRole("button", { name: "表格" });
      expect(cards).toHaveAttribute("aria-pressed", "true");
      expect(table).toHaveAttribute("aria-pressed", "false");
      expect(readWuiView()).toBe("cards");
    });

    it("switches to the table and back, and the choice survives a remount", async () => {
      const { container, unmount } = render(<WuiOverviewPage client={client()} />, { wrapper: Wrap });
      await screen.findByRole("region", { name: "根因分析" });

      fireEvent.click(screen.getByRole("button", { name: TABLE() }));

      expect(container.querySelector(".wui-list")).not.toBeNull();
      expect(container.querySelector(".wui-cards")).toBeNull();
      expect(container.querySelector(".page")).not.toHaveClass("page--wide");
      expect(screen.getByRole("button", { name: TABLE() })).toHaveAttribute("aria-pressed", "true");
      expect(readWuiView()).toBe("table");
      unmount();

      const again = render(<WuiOverviewPage client={client()} />, { wrapper: Wrap });
      await screen.findByRole("region", { name: "根因分析" });
      expect(again.container.querySelector(".wui-list")).not.toBeNull();

      fireEvent.click(screen.getByRole("button", { name: CARDS() }));
      expect(again.container.querySelector(".wui-cards")).not.toBeNull();
      expect(readWuiView()).toBe("cards");
    });

    it("has no toggle when there is nothing to list", async () => {
      render(<WuiOverviewPage client={client([])} />, { wrapper: Wrap });
      await screen.findByText(word("wui.empty"));
      expect(screen.queryByRole("button", { name: CARDS() })).toBeNull();
      expect(screen.queryByRole("button", { name: TABLE() })).toBeNull();
    });

    it("makes the whole card the page's link, with the star and Remove OUTSIDE it", async () => {
      render(
        <WuiOverviewPage
          client={client([row({ path: "/報表/出貨 看板/page.ai.yaml", title: "出貨看板" })])}
        />,
        { wrapper: Wrap },
      );
      const rca = await screen.findByRole("region", { name: "根因分析" });

      const card = rca.querySelector("li.wui-card")!;
      const link = within(card as HTMLElement).getByRole("link", { name: "出貨看板" });
      // The same address the table row and the pane spell, in a new tab.
      expect(link).toHaveAttribute(
        "href",
        "/w/rca/i-1/%E5%A0%B1%E8%A1%A8/%E5%87%BA%E8%B2%A8%20%E7%9C%8B%E6%9D%BF/page.ai.yaml",
      );
      expect(link).toHaveAttribute("target", "_blank");
      // The link is STRETCHED over the card by the sheet (`::after`), so the
      // buttons must not be its descendants — a button inside a link is not
      // HTML, and a press on it would open the page.
      const star = within(card as HTMLElement).getByRole("button", { name: "把「出貨看板」加入我的最愛" });
      const remove = within(card as HTMLElement).getByRole("button", { name: REMOVE() });
      expect(link.contains(star)).toBe(false);
      expect(link.contains(remove)).toBe(false);
      // Everything the table row says, the card says: the mark, the item, who
      // and when.
      expect(within(card as HTMLElement).getByTestId("page-mark")).toHaveTextContent("出");
      expect(card).toHaveTextContent("Line 3 stoppage");
      expect(card).toHaveTextContent("bob");
      // The item link the table row has, the card has too — reachable above
      // the stretched link, and not inside the title's link.
      const item = within(card as HTMLElement).getByRole("link", { name: "Line 3 stoppage" });
      expect(item).toHaveAttribute("href", "/a/rca/i-1");
      expect(link.contains(item)).toBe(false);
      // The star is the card's top-right corner (the author: 「我的最愛通常會
      // 在右上角」) — its own element, not in the footer with 下架.
      expect(star.closest(".wui-card > .star")).not.toBeNull();
      expect(remove.closest(".wui-card > .actions")).not.toBeNull();
      // A long title and a long detail are CUT, not shown whole (the author:
      // 「不要硬要顯示全部」): the whole text lives in the tooltip.
      expect(link).toHaveAttribute("title", "出貨看板");
      const detail = card.querySelector(".detail")!;
      // The fixture's stamp is 2023, so the relative form is the date, not
      // "just now" — derived with the shell's own `relativeTime`, since the
      // claim is "the same sentence as on screen", and the sentence's own
      // shape is pinned literally by the table test above.
      const when = relativeTime(new Date(1_700_000_000_000).toISOString());
      expect(detail).toHaveAttribute("title", `Line 3 stoppage · bob Deploy · ${when}`);
    });

    it("stars from a card flip both copies, and the favourites group is a card grid too", async () => {
      render(<WuiOverviewPage client={client()} />, { wrapper: Wrap });
      await screen.findByRole("region", { name: "根因分析" });

      fireEvent.click(screen.getByRole("button", { name: translate("zh-TW", "wui.star", { title: "Burn-down" }) }));

      const fav = screen.getByRole("region", { name: word("wui.favourites") });
      expect(fav.querySelector("ul.wui-cards")).not.toBeNull();
      const unstars = screen.getAllByRole("button", {
        name: translate("zh-TW", "wui.unstar", { title: "Burn-down" }),
      });
      expect(unstars).toHaveLength(2);
      for (const b of unstars) expect(b).toHaveAttribute("aria-pressed", "true");
    });

    it("removes from a card the way the table row does — asked once, one DELETE", async () => {
      const c = client();
      render(<WuiOverviewPage client={c} />, { wrapper: Wrap });
      const rca = await screen.findByRole("region", { name: "根因分析" });

      fireEvent.click(within(rca).getAllByRole("button", { name: REMOVE() })[0]);
      const dialog = await screen.findByRole("dialog");
      expect(dialog).toHaveTextContent("Shipping board");
      fireEvent.click(within(dialog).getByRole("button", { name: word("wui.remove") }));

      await waitFor(() =>
        expect(c.remove).toHaveBeenCalledWith("rca", "i-1", "/pages/report/page.ai.yaml"),
      );
      expect(c.remove).toHaveBeenCalledTimes(1);
      await waitFor(() => expect(screen.queryByRole("link", { name: "Shipping board" })).toBeNull());
    });
  });

  it("publishes its own breadcrumb trail, so the bar stops naming the item the viewer just left", async () => {
    // Seen in the demo: arriving from an item's workspace, the global bar on
    // /wui still read "Home › Playground › Line 3 yield review" — the trail is
    // "latest caller wins" (`hooks/breadcrumbs.tsx`), and this page had never
    // called. Help, Diagnostics, Review and the KB each publish `Home › <page>`.
    render(
      <BreadcrumbProvider>
        <WuiOverviewPage client={client([])} />
        <TrailProbe />
      </BreadcrumbProvider>,
      { wrapper: Wrap },
    );

    await screen.findByText(word("wui.empty"));
    const items = screen.getByTestId("trail").querySelectorAll("li");
    expect(Array.from(items).map((li) => li.textContent)).toEqual([word("nav.home"), "WUI"]);
    // Home is a link back; the leaf is where the viewer is.
    expect(items[0].getAttribute("data-to")).toBe("/");
    expect(items[1].getAttribute("data-to")).toBe("");
  });
});

/** Reads the trail the page published — the global bar's view of it. */
function TrailProbe() {
  const trail = useBreadcrumbTrail();
  return (
    <ul data-testid="trail">
      {trail.map((c, i) => (
        <li key={i} data-to={c.to ?? ""}>
          {c.label}
        </li>
      ))}
    </ul>
  );
}
