// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import type { AppItem, AppManifest, ItemSkillState } from "../../api/types";
import { translate } from "../../lib/i18n";
import { renderWithQuery } from "../../test/queryWrapper";
import { WorkspaceShell } from "./WorkspaceShell";

// plan-skill-hub-release-notes D7. Above the composer it lived in the chat
// column — and on a narrow screen an IDE-first item shows no chat column at
// all (#464), so nobody saw it there either. The user's call: the top of the
// item, under its title bar, in every layout. The chat is stubbed to nothing
// here, so the banner provably does not depend on it.
vi.mock("../../components/ItemChatShell", () => ({ ItemChatShell: () => null }));
vi.mock("../../components/PresenceBar", () => ({ PresenceBar: () => null }));
vi.mock("../../components/ActivityFeed", () => ({ ActivityFeed: () => null }));
vi.mock("../../hooks/useAgent", () => ({
  AgentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAgent: () => ({ log: { entries: [], streaming: false }, metrics: null }),
}));
vi.mock("../../hooks/useIsSuperuser", () => ({
  useIsSuperuser: () => false,
  useIsSuperuserState: () => ({ isSuperuser: false, ready: true }),
}));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => "carol",
  useCurrentUserState: () => ({ id: "carol", ready: true }),
}));

const word = (key: string, vars?: Record<string, string | number>) => translate("zh-TW", key as never, vars);

const manifest = {
  slug: "rca",
  title: "RCA",
  icon: "flame",
  color: "#000",
  function: { workspace: true, sandbox: false, terminal: false },
  agent: { picker: [] },
  item: { noun: "Investigation", noun_plural: "Investigations" },
  layout: { breadcrumb: [], statusbar: [], list: [], default_tabs: [], primary_surface: "ide", chat_switcher: false },
  labels: {},
  fields: [],
  field_styles: {},
  profiles: [],
  default_profile: "default",
  resource_route: "/rca-investigation",
} as unknown as AppManifest;

const BEHIND: ItemSkillState = {
  name: "log-digest",
  description: "",
  source: "workspace",
  default_on: true,
  is_copy: true,
  copy_of: "hub",
  hub_entry: "e1",
  update_available: true,
  upstream: "live",
  pref: "follow",
  effective: true,
  updates: [{ version: 2, kind: "publish", at: "2026-11-03T12:00:00Z", release_note: "Flags FATAL.", back_to: null }],
};

function open(grants: Record<string, string[]>) {
  const item = {
    resource_id: "INC-1",
    title: "Checkout incident",
    owner: "bob",
    created_by: "bob",
    permission: { visibility: "restricted", read_meta: ["user:carol"], read_chat: ["user:carol"], ...grants },
  } as unknown as AppItem;
  renderWithQuery(
    <MemoryRouter>
      <WorkspaceShell manifest={manifest} item={item} files={[]} />
    </MemoryRouter>,
  );
}

const banner = () => screen.findByRole("region", { name: word("skills.banner.label") });
const updateButton = (el: HTMLElement) =>
  within(el).queryByRole("button", { name: new RegExp(`^${word("skills.updates.update")}$`) });

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(api, "getItemSkills").mockResolvedValue([BEHIND] as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WorkspaceShell skill update banner", () => {
  it("is at the top of the item with no chat column on screen, with Update for an editor", async () => {
    open({ read_content: ["user:carol"], edit_content: ["user:carol"] });
    const b = await banner();
    expect(b).toHaveTextContent(word("skills.banner.one", { name: "log-digest", version: 2 }));
    fireEvent.click(within(b).getByRole("button", { name: word("skills.updates.show") }));
    expect(updateButton(await banner())).toBeInTheDocument();
  });

  it("shows the notes but no Update to someone who may only read", async () => {
    open({ read_content: ["user:carol"] });
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    const b = await banner();
    expect(b).toHaveTextContent("Flags FATAL.");
    expect(updateButton(b)).toBeNull();
  });
});
