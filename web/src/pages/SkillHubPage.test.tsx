// @vitest-environment happy-dom
/**
 * The skill hub list (`docs/plan-skill-hub.md`): roots with forks beneath,
 * search + 「我的」 as SERVER parameters, and the two empty states.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
import { QueryWrap } from "../test/queryWrapper";
import { SkillHubPage } from "./SkillHubPage";

const word = (key: Parameters<typeof translate>[1]) => translate("zh-TW", key);

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
  const list = vi.fn<SkillHubApi["list"]>(async (q = "", mine = false) =>
    entries.filter(
      (e) =>
        (!mine || e.is_mine) &&
        (!q || e.name.includes(q.toLowerCase()) || e.description.toLowerCase().includes(q.toLowerCase())),
    ),
  );
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
    // The root says how many forks it has; the fork with review notes says so.
    expect(within(root).getByText(word("skillHub.fork.one"))).toBeInTheDocument();
    expect(within(fork).getByText(word("skillHub.badge.notes"))).toBeInTheDocument();
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
