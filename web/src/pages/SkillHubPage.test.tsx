// @vitest-environment happy-dom
/**
 * The skill hub list (docs/plan-skill-hub-ux-redo.md D1/D2/D4/D15/D17): one
 * compact row per skill, originals only while browsing, search / 「我的」 /
 * owner / sort as SERVER parameters kept in the address, a page of 50 with
 * 「載入更多」 and the total, and the empty states.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SkillHubCard, SkillHubListing } from "../api/skillHub";

vi.mock("../api", () => ({
  api: {
    listApps: vi.fn(async () => [
      { slug: "rca", title: "根因分析", description: "", icon: "flame", color: "#F0502E" },
    ]),
    getUsers: vi.fn(async () => [
      { id: "alice", name: "Alice Wu", section: "", email: "", photo_url: null },
    ]),
  },
}));

import { translate } from "../lib/i18n";
import { makeQueryClient } from "../api/queryClient";
import { QueryWrap } from "../test/queryWrapper";
import { fakeSkillHub, hubCard as card, matching } from "../test/skillHubFake";
import { SkillHubPage } from "./SkillHubPage";

const word = (
  key: Parameters<typeof translate>[1],
  vars?: Record<string, string | number>,
) => translate("zh-TW", key, vars);

const ROOT = card({});
const FORK = card({
  id: "e-fork",
  owner: "bob",
  forked_from: "e-root",
  origin: { owner: "alice", name: "triage-reflow" },
  review_verdict: "notes",
});
const OTHER = card({ id: "e-other", owner: "carol", name: "deck-maker", description: "Slides." });

const client = (entries: SkillHubCard[] = [ROOT, FORK, OTHER], since = "2026-10-07") =>
  fakeSkillHub(entries, since);

/** What the address says, for the tests that check the filters live there. */
let where = "";
function Spy() {
  const loc = useLocation();
  where = loc.search;
  return null;
}

function Wrap({ children, at = "/skill-hub" }: { children: React.ReactNode; at?: string }) {
  return (
    <MemoryRouter initialEntries={[at]}>
      <QueryWrap>
        {children}
        <Spy />
      </QueryWrap>
    </MemoryRouter>
  );
}

const answer = (entries: SkillHubCard[], query = {}): SkillHubListing => {
  const rows = matching(entries, query);
  return { entries: rows, total: rows.length, counted_since: "2026-10-07" };
};

afterEach(cleanup);

describe("SkillHubPage", () => {
  it("lists originals only, one row each, and says how many forks an original has (D1, D2)", async () => {
    render(<SkillHubPage client={client()} />, { wrapper: Wrap });

    const root = await screen.findByTestId("entry-e-root");
    expect(screen.queryByTestId("entry-e-fork")).toBeNull();
    expect(within(root).getByRole("link", { name: "triage-reflow" })).toHaveAttribute(
      "href",
      "/skill-hub/e-root",
    );
    // The fork count is a link to the original's fork tab (D15).
    expect(within(root).getByRole("link", { name: word("skillHub.fork.one") })).toHaveAttribute(
      "href",
      "/skill-hub/e-root?tab=forks",
    );
    // The owner is said once, not as an `owner/` prefix beside a chip as well.
    expect(within(root).queryByText("alice/")).toBeNull();
    // No App tag on a row: it said the same thing on every row (D15).
    expect(within(root).queryByText("根因分析")).toBeNull();
  });

  it("says the total and pages fifty at a time with 「載入更多」 (D4)", async () => {
    const many = Array.from({ length: 51 }, (_, n) =>
      card({ id: `e-${String(n).padStart(2, "0")}`, name: `s${String(n).padStart(2, "0")}` }),
    );
    const c = client(many);
    render(<SkillHubPage client={c} />, { wrapper: Wrap });

    await screen.findByTestId("entry-e-49");
    expect(screen.queryByTestId("entry-e-50")).toBeNull();
    expect(screen.getByText(word("skillHub.total", { count: 51 }))).toBeInTheDocument();
    expect(screen.getByText(word("skillHub.shown", { shown: 50, total: 51 }))).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: word("skillHub.loadMore") }));

    expect(await screen.findByTestId("entry-e-50")).toBeInTheDocument();
    expect(c.browse).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 }));
    expect(screen.queryByRole("button", { name: word("skillHub.loadMore") })).toBeNull();
    expect(screen.getByTestId("entry-e-00")).toBeInTheDocument(); // added to, not replaced
  });

  it("shows counts only when there are some, and since when they were counted (D17, U6)", async () => {
    const busy = card({ id: "e-busy", name: "busy", installs: 3, uses: 12 });
    const quiet = card({ id: "e-quiet", name: "quiet" });
    render(<SkillHubPage client={client([busy, quiet])} />, { wrapper: Wrap });

    const row = await screen.findByTestId("entry-e-busy");
    expect(
      within(row).getByText(word("skillHub.counts", { installs: 3, uses: 12 })),
    ).toBeInTheDocument();
    expect(within(screen.getByTestId("entry-e-quiet")).queryByText(/安裝 0 次/)).toBeNull();
    expect(screen.getByText(word("skillHub.countedSince", { day: "2026-10-07" }))).toBeInTheDocument();
  });

  it("shows when a skill's content was last updated, and nothing for one with no date", async () => {
    const dated = card({ id: "e-dated", name: "dated", updated_at: "2026-10-02T09:00:00Z" });
    const undated = card({ id: "e-undated", name: "undated" });
    render(<SkillHubPage client={client([dated, undated])} />, { wrapper: Wrap });

    const row = await screen.findByTestId("entry-e-dated");
    expect(within(row).getByText(word("skillHub.updated", { day: "2026/10/02" }))).toBeInTheDocument();
    expect(within(screen.getByTestId("entry-e-undated")).queryByText(/更新/)).toBeNull();
  });

  it("opening the page sends one listing request", async () => {
    const c = client();
    render(<SkillHubPage client={c} />, { wrapper: Wrap });
    await screen.findByTestId("entry-e-root");
    expect(c.browse).toHaveBeenCalledTimes(1);
  });

  it("says nothing about since when before anything was counted", async () => {
    render(<SkillHubPage client={client([OTHER], "")} />, { wrapper: Wrap });
    await screen.findByTestId("entry-e-other");
    expect(screen.queryByText(/自 .* 起|since/)).toBeNull();
  });

  it("sorts by name, most used or recently updated — a labelled choice kept in the address (D4)", async () => {
    const quiet = card({ id: "e-a", name: "aaa", uses: 1, updated_at: "2026-10-03T00:00:00Z" });
    const busy = card({ id: "e-z", name: "zzz", uses: 9, updated_at: "2026-10-01T00:00:00Z" });
    const c = client([quiet, busy]);
    render(<SkillHubPage client={c} />, { wrapper: Wrap });
    await screen.findByTestId("entry-e-a");
    const order = () =>
      screen.getAllByTestId(/^entry-e-[az]$/).map((el) => el.getAttribute("data-testid"));
    expect(order()).toEqual(["entry-e-a", "entry-e-z"]);

    const sort = screen.getByRole("combobox", { name: word("skillHub.sort") });
    fireEvent.change(sort, { target: { value: "popular" } });
    await waitFor(() => expect(order()).toEqual(["entry-e-z", "entry-e-a"]));
    expect(where).toContain("sort=popular");

    fireEvent.change(sort, { target: { value: "updated" } });
    await waitFor(() =>
      expect(c.browse).toHaveBeenLastCalledWith(expect.objectContaining({ sort: "updated" })),
    );
    await waitFor(() => expect(order()).toEqual(["entry-e-a", "entry-e-z"]));
  });

  it("restores the filters from the address — Back to the list lands where you were (D4)", async () => {
    const c = client();
    render(
      <Wrap at="/skill-hub?q=deck&sort=popular">
        <SkillHubPage client={c} />
      </Wrap>,
    );
    await screen.findByTestId("entry-e-other");
    expect(c.browse).toHaveBeenCalledWith(expect.objectContaining({ q: "deck", sort: "popular" }));
    expect(screen.getByRole("searchbox")).toHaveValue("deck");
  });

  it("a search lists forks beside originals, each saying what it was forked from (D2)", async () => {
    const c = client();
    render(<SkillHubPage client={c} />, { wrapper: Wrap });
    await screen.findByTestId("entry-e-root");

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "triage" } });

    const fork = await screen.findByTestId("entry-e-fork");
    expect(
      within(fork).getByText(word("skillHub.forkOf", { origin: "alice/triage-reflow" })),
    ).toBeInTheDocument();
    expect(where).toContain("q=triage");
  });

  it("a fork whose original the viewer cannot read says only that (D2)", async () => {
    const orphan = card({ id: "e-orphan", owner: "me", forked_from: "e-gone", is_mine: true });
    render(<SkillHubPage client={client([orphan])} />, { wrapper: Wrap });

    const row = await screen.findByTestId("entry-e-orphan");
    expect(within(row).getByText(word("skillHub.forkOf.gone"))).toBeInTheDocument();
  });

  it("「我的」 and an owner are server parameters; an owner's name on a row filters to them (D4)", async () => {
    const c = client();
    render(<SkillHubPage client={c} />, { wrapper: Wrap });
    const other = await screen.findByTestId("entry-e-other");

    fireEvent.click(within(other).getByRole("link", { name: word("skillHub.owner.only", { name: "carol" }) }));

    await waitFor(() =>
      expect(c.browse).toHaveBeenLastCalledWith(expect.objectContaining({ owner: "carol" })),
    );
    await waitFor(() => expect(screen.queryByTestId("entry-e-root")).toBeNull());
    expect(where).toContain("owner=carol");
    fireEvent.click(screen.getByRole("button", { name: word("skillHub.owner.clear") }));
    expect(await screen.findByTestId("entry-e-root")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: word("skillHub.mine") }));
    await waitFor(() =>
      expect(c.browse).toHaveBeenLastCalledWith(expect.objectContaining({ mine: true })),
    );
  });

  it("when a search finds nothing, says what was searched and offers to clear it (D17)", async () => {
    render(<SkillHubPage client={client()} />, { wrapper: Wrap });
    await screen.findByTestId("entry-e-root");

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "zzz" } });

    expect(await screen.findByText(word("skillHub.noMatch.query", { q: "zzz" }))).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: word("skillHub.clearSearch") }));
    expect(await screen.findByTestId("entry-e-root")).toBeInTheDocument();
    expect(screen.getByRole("searchbox")).toHaveValue("");
  });

  it("keeps the search box mounted and focused while a new query is fetched (plan-skill-hub-ui-polish D2)", async () => {
    // Every new (q, mine) key used to be `isPending`, and the whole page —
    // search box included — was swapped for 載入中…: the box remounted after
    // each debounce, and the caret and focus went with it. The list is
    // what loads; the controls stay, holding the previous list meanwhile.
    const c = client();
    let release: (() => void) | undefined;
    c.browse.mockImplementation(
      (query) =>
        new Promise<SkillHubListing>((resolve) => {
          const reply = () => resolve(answer([ROOT, FORK, OTHER], query));
          if (!query.q) reply();
          else release = reply; // the search is answered only when the test says
        }),
    );
    render(<SkillHubPage client={c} />, { wrapper: Wrap });
    await screen.findByTestId("entry-e-root");
    const box = screen.getByRole("searchbox");
    box.focus();
    fireEvent.change(box, { target: { value: "deck" } });
    await waitFor(() =>
      expect(c.browse).toHaveBeenCalledWith(expect.objectContaining({ q: "deck" })),
    );

    // mid-fetch: same node, still focused, previous rows still there — and
    // the results area SAYS it is loading (review round 1: nothing did).
    expect(screen.getByRole("searchbox")).toBe(box);
    expect(document.activeElement).toBe(box);
    expect(screen.getByTestId("entry-e-root")).toBeInTheDocument();
    expect(screen.getByTestId("skill-hub-results")).toHaveAttribute(
      "aria-busy",
      "true",
    );

    release?.();
    await waitFor(() =>
      expect(screen.queryByTestId("entry-e-root")).toBeNull(),
    );
    expect(screen.getByRole("searchbox")).toBe(box);
    expect(document.activeElement).toBe(box);
    expect(screen.getByTestId("skill-hub-results")).toHaveAttribute(
      "aria-busy",
      "false",
    );
  });

  it("keeps the tools when Retry follows a failed first load — the loading text sits in the results area (review round 3)", async () => {
    // TanStack resets a data-less errored query to `pending` for the refetch,
    // and the whole-tree loading line came back — the Retry button the
    // person had just pressed gone with it. Only a page that never settled
    // (no result, no error yet) gets the whole-tree line.
    const c = client();
    let first = true;
    c.browse.mockImplementation(
      () =>
        new Promise<SkillHubListing>((_resolve, reject) => {
          if (first) {
            first = false;
            reject(new Error("boom"));
          }
        }),
    );
    render(<SkillHubPage client={c} />, { wrapper: Wrap });
    const alert = await screen.findByRole("alert");
    const box = screen.getByRole("searchbox");
    fireEvent.click(within(alert).getByRole("button", { name: word("skillHub.retry") }));

    await waitFor(() => expect(c.browse).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("searchbox")).toBe(box);
    expect(screen.getByTestId("skill-hub-results")).toHaveTextContent(word("skillHub.loading"));
  });

  it("keeps the tools when the first load failed and a search follows — the loading text sits in the results area (D2, review round 2)", async () => {
    // After a failed first load no query ever had data, so the next key has
    // no previous data to keep and is `isPending` — and the page swapped the
    // whole tree for 載入中… again, search box included. The whole-tree line is
    // for the untouched page only; once the tools were used, loading is a
    // state of the results area.
    const c = client();
    let first = true;
    c.browse.mockImplementation(
      () =>
        new Promise<SkillHubListing>((_resolve, reject) => {
          if (first) {
            first = false;
            reject(new Error("boom"));
          } // the second one hangs — mid-fetch is the state under test
        }),
    );
    render(<SkillHubPage client={c} />, { wrapper: Wrap });
    await screen.findByRole("alert");
    const box = screen.getByRole("searchbox");
    fireEvent.change(box, { target: { value: "x" } });

    await waitFor(() =>
      expect(c.browse).toHaveBeenCalledWith(expect.objectContaining({ q: "x" })),
    );
    expect(screen.getByRole("searchbox")).toBe(box);
    expect(screen.getByTestId("skill-hub-results")).toHaveTextContent(word("skillHub.loading"));
  });

  it("shows the error, not the empty state, when an empty hub's refetch fails (review round 2)", async () => {
    // `everything` and the list share a key; TanStack keeps `[]` as data on a
    // failed refetch, so `nothingPublished` was true while `isError` was too
    // and the "nothing published" state hid the error and its Retry.
    const c = client([]);
    const qc = makeQueryClient();
    render(
      <MemoryRouter>
        <QueryWrap client={qc}>
          <SkillHubPage client={c} />
        </QueryWrap>
      </MemoryRouter>,
    );
    await screen.findByText(word("skillHub.empty"));
    c.browse.mockRejectedValue(new Error("boom"));
    await qc.invalidateQueries({ queryKey: ["skillHub"] });

    expect(await screen.findByRole("alert")).toHaveTextContent(word("skillHub.error"));
    expect(screen.queryByText(word("skillHub.empty"))).toBeNull();
  });

  it("keeps the search box and the tools when a search fails — the error and Retry sit in the results area (D2, review round 1)", async () => {
    const c = client();
    c.browse.mockImplementation(async (query) => {
      if (query.q) throw new Error("boom");
      return answer([ROOT, FORK, OTHER]);
    });
    render(<SkillHubPage client={c} />, { wrapper: Wrap });
    await screen.findByTestId("entry-e-root");
    const box = screen.getByRole("searchbox");
    fireEvent.change(box, { target: { value: "deck" } });

    const alert = await screen.findByRole("alert");
    expect(screen.getByRole("searchbox")).toBe(box);
    expect(screen.getByTestId("skill-hub-results")).toContainElement(alert);
    const calls = c.browse.mock.calls.length;
    fireEvent.click(
      within(alert).getByRole("button", { name: word("skillHub.retry") }),
    );
    await waitFor(() =>
      expect(c.browse.mock.calls.length).toBeGreaterThan(calls),
    );
  });

  it("shows the notice a navigation handed it, as a status the person can dismiss (D10)", async () => {
    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: "/skill-hub",
            state: { notice: { kind: "success", text: "已刪除「x」。" } },
          },
        ]}
      >
        <QueryWrap>
          <SkillHubPage client={client()} />
        </QueryWrap>
      </MemoryRouter>,
    );
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("已刪除「x」。");
    fireEvent.click(
      within(status).getByRole("button", { name: word("notice.dismiss") }),
    );
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("says the notice once — Back to the list does not announce it again (D10, review round 1)", async () => {
    // The notice rode in the history entry's state, so opening an entry and
    // pressing Back re-mounted the list with the same state: 「已刪除「x」。」
    // again, for a delete that happened a while ago. The page consumes it on
    // arrival (the entry's state is replaced with none).
    function Away() {
      const navigate = useNavigate();
      return (
        <button type="button" onClick={() => navigate(-1)}>
          back
        </button>
      );
    }
    render(
      <MemoryRouter
        initialEntries={[
          { pathname: "/skill-hub", state: { notice: { kind: "success", text: "已刪除「x」。" } } },
        ]}
      >
        <QueryWrap>
          <Routes>
            <Route
              path="/skill-hub"
              element={
                <>
                  <SkillHubPage client={client()} />
                  <Link to="/skill-hub/x">open one</Link>
                </>
              }
            />
            <Route path="/skill-hub/x" element={<Away />} />
          </Routes>
        </QueryWrap>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("status")).toHaveTextContent("已刪除「x」。");
    fireEvent.click(screen.getByRole("link", { name: "open one" }));
    fireEvent.click(await screen.findByRole("button", { name: "back" }));

    await screen.findByRole("searchbox");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows the empty state, without tools, when nobody has published anything", async () => {
    render(<SkillHubPage client={client([])} />, { wrapper: Wrap });

    expect(await screen.findByText(word("skillHub.empty"))).toBeInTheDocument();
    expect(screen.queryByRole("searchbox")).toBeNull();
  });

  it("offers a retry when the listing cannot be read", async () => {
    const c = client();
    c.browse.mockRejectedValueOnce(new Error("boom"));
    render(<SkillHubPage client={c} />, { wrapper: Wrap });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(word("skillHub.error"));
    fireEvent.click(within(alert).getByRole("button", { name: word("skillHub.retry") }));
    expect(await screen.findByTestId("entry-e-root")).toBeInTheDocument();
  });
});
