// @vitest-environment happy-dom
/**
 * The chat card `show_skill_hub_entry` declares (plan-skill-hub-history §8, A3):
 * live — the entry is read when drawn, and 〔安裝〕 goes through the Skills
 * panel's own door. "Installed" is decided the way the panel's picker decides
 * a name is taken (`filesHere`), from the same skills query.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "../api/http";
import type { SkillHubApi, SkillHubDetail } from "../api/skillHub";
import type { ItemSkillState } from "../api/types";
import { ChatItemProvider } from "../hooks/chatItem";
import { translate } from "../lib/i18n";
import { QueryWrap } from "../test/queryWrapper";
import { SkillHubEntryCard } from "./SkillHubEntryCard";

const word = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);

const detail = (over: Partial<SkillHubDetail> = {}): SkillHubDetail => ({
  id: "e-1",
  owner: "alice",
  name: "triage-reflow",
  description: "Triage reflow defects.",
  source_app: "rca",
  source_profile: "default",
  source_item: "",
  referenced_tools: ["exec", "query_entity"],
  review: { verdict: "notes", notes: ["step 2 needs exec"], model: "m" },
  forked_from: null,
  forks: [],
  files: ["SKILL.md"],
  skill_md: "",
  is_owner: false,
  visibility: "public",
  permission: null,
  missing_tools: ["query_entity"],
  installs: 3,
  uses: 8,
  counted_since: "2026-10-07",
  ...over,
});

const skill = (over: Partial<ItemSkillState>): ItemSkillState => ({
  name: "x",
  description: "",
  source: "workspace",
  default_on: true,
  pref: "follow",
  effective: true,
  ...over,
});

function setup({
  entry = detail(),
  skills = [] as ItemSkillState[],
  item = true,
  get,
  install,
}: {
  entry?: SkillHubDetail;
  skills?: ItemSkillState[];
  item?: boolean;
  get?: SkillHubApi["get"];
  install?: SkillHubApi["install"];
} = {}) {
  let have = skills;
  const hub = {
    get: vi.fn<SkillHubApi["get"]>(get ?? (async () => entry)),
    install: vi.fn<SkillHubApi["install"]>(
      install ??
        (async () => {
          have = [...have, skill({ name: entry.name, copy_of: "hub", is_copy: false })];
          return { name: entry.name, missing_tools: [] };
        }),
    ),
  };
  const items = { getItemSkills: vi.fn(async () => have) };
  const card = <SkillHubEntryCard entryId="e-1" client={hub} skillsClient={items} />;
  render(
    <MemoryRouter>
      <QueryWrap>
        {item ? <ChatItemProvider value={{ slug: "pm", itemId: "inv-1" }}>{card}</ChatItemProvider> : card}
      </QueryWrap>
    </MemoryRouter>,
  );
  return { hub, items };
}

afterEach(cleanup);

describe("SkillHubEntryCard", () => {
  it("shows the entry as this App sees it, with its counts, the tools it lacks and the review", async () => {
    const { hub } = setup();

    const link = await screen.findByRole("link", { name: /alice\/\s*triage-reflow/ });
    expect(link).toHaveAttribute("href", "/skill-hub/e-1");
    expect(hub.get).toHaveBeenCalledWith("e-1", "pm");
    expect(screen.getByText("Triage reflow defects.")).toBeInTheDocument();
    expect(screen.getByText(word("skillHub.counts", { installs: 3, uses: 8 }))).toBeInTheDocument();
    // The tools this App lacks, in the picker's own sentence.
    expect(
      screen.getByText(word("skills.fromHub.missing", { tools: "query_entity" })),
    ).toBeInTheDocument();
    expect(screen.getByText("step 2 needs exec")).toBeInTheDocument();
  });

  it("says the review had nothing to add when it had no notes", async () => {
    setup({ entry: detail({ review: { verdict: "ok", notes: [], model: "m" } }) });
    expect(await screen.findByText(word("skillHub.card.reviewOk"))).toBeInTheDocument();
  });

  it("installs through the Skills panel's door and then reads as installed", async () => {
    const { hub, items } = setup();

    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.card.install") }));

    await waitFor(() => expect(hub.install).toHaveBeenCalledWith("pm", "inv-1", "e-1"));
    expect(await screen.findByText(word("skillHub.card.installed"))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: word("skillHub.card.install") })).toBeNull();
    expect(items.getItemSkills).toHaveBeenCalledTimes(2);
  });

  it("reads as installed when a folder of that name is already here — the picker's rule", async () => {
    setup({ skills: [skill({ name: "triage-reflow", source: "workspace" })] });
    expect(await screen.findByText(word("skillHub.card.installed"))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: word("skillHub.card.install") })).toBeNull();
  });

  it("does not count a package skill of that name as installed — its files are not here", async () => {
    setup({ skills: [skill({ name: "triage-reflow", source: "profile", is_copy: false })] });
    expect(await screen.findByRole("button", { name: word("skillHub.card.install") })).toBeInTheDocument();
  });

  it("words a refusal the way the Skills panel does", async () => {
    setup({
      install: async () => {
        throw new HttpError(409, "install failed (409)", "folder_in_the_way", undefined, {
          error: "folder_in_the_way",
          path: ".skill/triage-reflow",
        });
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.card.install") }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      word("skillHub.refused.folder_in_the_way", { path: ".skill/triage-reflow" }),
    );
  });

  it("offers no install where there is no item — the card still shows the entry", async () => {
    const { hub, items } = setup({ item: false });
    await screen.findByRole("link", { name: /alice\/\s*triage-reflow/ });
    expect(hub.get).toHaveBeenCalledWith("e-1", "");
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText(word("skillHub.card.installed"))).toBeNull();
    expect(items.getItemSkills).not.toHaveBeenCalled();
  });

  it("says the skill is gone when the viewer can no longer read it", async () => {
    setup({
      get: async () => {
        throw new HttpError(404, "the skill hub entry could not be read (404)");
      },
    });
    expect(await screen.findByText(word("skillHub.card.gone"))).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
