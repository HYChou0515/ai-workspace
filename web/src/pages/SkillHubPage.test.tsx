// @vitest-environment happy-dom
/**
 * The skill hub list (`docs/plan-skill-hub.md`): roots with forks beneath,
 * search + 「我的」 as SERVER parameters, and the two empty states.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SkillHubApi, SkillHubCard } from "../api/skillHub";

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
import { SkillHubPage } from "./SkillHubPage";

const word = (
  key: Parameters<typeof translate>[1],
  vars?: Record<string, string | number>,
) => translate("zh-TW", key, vars);

const card = (over: Partial<SkillHubCard>): SkillHubCard => ({
  id: "e-root",
  owner: "alice",
  name: "triage-reflow",
  description: "Triage reflow defects.",
  source_app: "rca",
  referenced_tools: ["exec"],
  forked_from: "",
  review_verdict: "ok",
  is_mine: false,
  missing_tools: [],
  forks: [],
  ...over,
});

const ROOT_WITH_FORK = card({
  forks: [card({ id: "e-fork", owner: "bob", forked_from: "e-root", review_verdict: "notes" })],
});
const OTHER = card({ id: "e-other", owner: "carol", name: "deck-maker", description: "Slides." });

function client(entries: SkillHubCard[] = [ROOT_WITH_FORK, OTHER]) {
  // The server's shape (`nest_forks`): a hit nests under its root only when
  // the root is a hit too; otherwise it is a root of its own — so 「我的」
  // lifts a fork of somebody else's entry out to the top level.
  const hit = (e: SkillHubCard, q: string, mine: boolean) =>
    (!mine || e.is_mine) &&
    (!q ||
      e.name.includes(q.toLowerCase()) ||
      e.description.toLowerCase().includes(q.toLowerCase()));
  const list = vi.fn<SkillHubApi["list"]>(async (q = "", mine = false) => {
    const roots = entries
      .filter((e) => hit(e, q, mine))
      .map((e) => ({ ...e, forks: e.forks.filter((f) => hit(f, q, mine)) }));
    const lifted = entries
      .filter((e) => !hit(e, q, mine))
      .flatMap((e) => e.forks.filter((f) => hit(f, q, mine)))
      .map((f) => ({ ...f, forks: [] }));
    return [...roots, ...lifted];
  });
  return {
    list,
    get: vi.fn<SkillHubApi["get"]>(),
    install: vi.fn<SkillHubApi["install"]>(),
    unpublish: vi.fn<SkillHubApi["unpublish"]>(),
    republish: vi.fn<SkillHubApi["republish"]>(),
    setPermission: vi.fn<SkillHubApi["setPermission"]>(),
    remove: vi.fn<SkillHubApi["remove"]>(),
    transfer: vi.fn<SkillHubApi["transfer"]>(),
    edit: vi.fn<SkillHubApi["edit"]>(),
  } satisfies SkillHubApi;
}

function Wrap({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter>
      <QueryWrap>{children}</QueryWrap>
    </MemoryRouter>
  );
}

afterEach(cleanup);

describe("SkillHubPage", () => {
  it("lists roots with their forks nested beneath, each linking to its page", async () => {
    render(<SkillHubPage client={client()} />, { wrapper: Wrap });

    const root = await screen.findByTestId("entry-e-root");
    const fork = within(root).getByTestId("entry-e-fork");
    expect(fork).toHaveAttribute("data-fork", "true");
    expect(within(root).getByRole("link", { name: /alice\/\s*triage-reflow/ })).toHaveAttribute(
      "href",
      "/skill-hub/e-root",
    );
    expect(within(fork).getByRole("link", { name: /bob\/\s*triage-reflow/ })).toHaveAttribute(
      "href",
      "/skill-hub/e-fork",
    );
    // The root says how many forks it has; the fork says whose it is.
    expect(within(root).getByText(word("skillHub.fork.one"))).toBeInTheDocument();
    expect(within(fork).getByText(
        word("skillHub.forkOf", { origin: "alice/triage-reflow" }),
      ),).toBeInTheDocument();
    // The other root is NOT under the first.
    expect(within(root).queryByTestId("entry-e-other")).toBeNull();
    expect(screen.getByTestId("entry-e-other")).toBeInTheDocument();
  });

  it("sends the search to the server, debounced, and 「我的」 as a parameter", async () => {
    const c = client();
    render(<SkillHubPage client={c} />, { wrapper: Wrap });
    await screen.findByTestId("entry-e-root");

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "deck" } });
    await waitFor(() => expect(c.list).toHaveBeenCalledWith("deck", false));
    await waitFor(() => expect(screen.queryByTestId("entry-e-root")).toBeNull());
    expect(screen.getByTestId("entry-e-other")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: word("skillHub.mine") }));
    await waitFor(() => expect(c.list).toHaveBeenCalledWith("deck", true));
  });

  it("keeps the search box mounted and focused while a new query is fetched (plan-skill-hub-ui-polish D2)", async () => {
    // Every new (q, mine) key used to be `isPending`, and the whole page —
    // search box included — was swapped for 載入中…: the box remounted after
    // each debounce, and the caret and focus went with it. The list is
    // what loads; the controls stay, holding the previous list meanwhile.
    const c = client();
    let release: (() => void) | undefined;
    c.list.mockImplementation(
      (q = "", mine = false) =>
        new Promise<SkillHubCard[]>((resolve) => {
          const answer = () =>
            resolve(
              [ROOT_WITH_FORK, OTHER].filter(
                (e) => (!mine || e.is_mine) && (!q || e.name.includes(q)),
              ),
            );
          if (!q && !mine) answer();
          else release = answer; // the search is answered only when the test says
        }),
    );
    render(<SkillHubPage client={c} />, { wrapper: Wrap });
    await screen.findByTestId("entry-e-root");
    const box = screen.getByRole("searchbox");
    box.focus();
    fireEvent.change(box, { target: { value: "deck" } });
    await waitFor(() => expect(c.list).toHaveBeenCalledWith("deck", false));

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
    c.list.mockImplementation(
      () =>
        new Promise<SkillHubCard[]>((_resolve, reject) => {
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

    await waitFor(() => expect(c.list).toHaveBeenCalledTimes(2));
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
    c.list.mockImplementation(
      () =>
        new Promise<SkillHubCard[]>((_resolve, reject) => {
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

    await waitFor(() => expect(c.list).toHaveBeenCalledWith("x", false));
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
    c.list.mockRejectedValue(new Error("boom"));
    await qc.invalidateQueries({ queryKey: ["skillHub"] });

    expect(await screen.findByRole("alert")).toHaveTextContent(word("skillHub.error"));
    expect(screen.queryByText(word("skillHub.empty"))).toBeNull();
  });

  it("keeps the search box and the tools when a search fails — the error and Retry sit in the results area (D2, review round 1)", async () => {
    const c = client();
    c.list.mockImplementation(async (q = "") => {
      if (q) throw new Error("boom");
      return [ROOT_WITH_FORK, OTHER];
    });
    render(<SkillHubPage client={c} />, { wrapper: Wrap });
    await screen.findByTestId("entry-e-root");
    const box = screen.getByRole("searchbox");
    fireEvent.change(box, { target: { value: "deck" } });

    const alert = await screen.findByRole("alert");
    expect(screen.getByRole("searchbox")).toBe(box);
    expect(screen.getByTestId("skill-hub-results")).toContainElement(alert);
    const calls = c.list.mock.calls.length;
    fireEvent.click(
      within(alert).getByRole("button", { name: word("skillHub.retry") }),
    );
    await waitFor(() =>
      expect(c.list.mock.calls.length).toBeGreaterThan(calls),
    );
  });

  it("does not badge an entry for having review notes — every entry was reviewed (D7)", async () => {
    render(<SkillHubPage client={client()} />, { wrapper: Wrap });
    const fork = await screen.findByTestId("entry-e-fork"); // review_verdict: notes
    expect(within(fork).queryByText("有審查意見")).toBeNull();
    expect(screen.queryByText("有審查意見")).toBeNull();
  });

  it("says where a fork came from on its own card, in 「我的」 too, where its root is out of view (D9)", async () => {
    const mine = card({
      id: "e-mine-fork",
      owner: "me",
      name: "triage-reflow",
      forked_from: "e-root",
      is_mine: true,
    });
    const gone = card({
      id: "e-orphan",
      owner: "me",
      name: "old-fork",
      forked_from: "e-vanished",
      is_mine: true,
    });
    const c = client([card({ forks: [mine] }), gone]);
    render(<SkillHubPage client={c} />, { wrapper: Wrap });
    await screen.findByTestId("entry-e-mine-fork");

    fireEvent.click(
      screen.getByRole("button", { name: word("skillHub.mine") }),
    );
    // …and only once the mine-only list has landed (the root card gone): the
    // previous list is kept on screen meanwhile, and asserting on it would
    // pass with the root right there.
    await waitFor(() =>
      expect(screen.queryByTestId("entry-e-root")).toBeNull(),
    );
    const row = screen.getByTestId("entry-e-mine-fork");
    expect(
      within(row).getByText(/fork 自 alice\/triage-reflow/),
    ).toBeInTheDocument();
    const orphan = screen.getByTestId("entry-e-orphan");
    expect(
      within(orphan).getByText(word("skillHub.forkOf.gone")),
    ).toBeInTheDocument();
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

  it("says so when nothing matches, and keeps the tools", async () => {
    render(<SkillHubPage client={client()} />, { wrapper: Wrap });
    await screen.findByTestId("entry-e-root");

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "zzz" } });

    expect(await screen.findByText(word("skillHub.noMatch"))).toBeInTheDocument();
    expect(screen.getByRole("searchbox")).toBeInTheDocument();
  });

  it("shows the empty state, without tools, when nobody has published anything", async () => {
    render(<SkillHubPage client={client([])} />, { wrapper: Wrap });

    expect(await screen.findByText(word("skillHub.empty"))).toBeInTheDocument();
    expect(screen.queryByRole("searchbox")).toBeNull();
  });

  it("offers a retry when the listing cannot be read", async () => {
    const c = client();
    c.list.mockRejectedValueOnce(new Error("boom"));
    render(<SkillHubPage client={c} />, { wrapper: Wrap });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(word("skillHub.error"));
    fireEvent.click(within(alert).getByRole("button", { name: word("skillHub.retry") }));
    expect(await screen.findByTestId("entry-e-root")).toBeInTheDocument();
  });
});
