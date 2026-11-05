// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import { renderWithQuery } from "../../test/queryWrapper";
import { WorkspaceShell } from "./WorkspaceShell";

// Everything live is stubbed to a marker — these tests are about ONE thing:
// which shell panels are showing, and what opens or retracts them.
vi.mock("../../components/ItemChatShell", () => ({
  ItemChatShell: ({ oneConversation }: { oneConversation?: boolean }) => (
    <div data-testid="chat" data-one={String(!!oneConversation)} />
  ),
}));
vi.mock("../../components/PresenceBar", () => ({ PresenceBar: () => null }));
vi.mock("../../components/ActivityFeed", () => ({ ActivityFeed: () => null }));
vi.mock("./TerminalPane", () => ({ TerminalPane: () => null }));
vi.mock("../../hooks/useAgent", () => ({
  AgentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAgent: () => ({ log: { entries: [], streaming: false }, metrics: null }),
}));
vi.mock("../../hooks/useIsSuperuser", () => ({
  useIsSuperuser: () => true,
  useIsSuperuserState: () => ({ isSuperuser: true, ready: true }),
}));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => "root",
  useCurrentUserState: () => ({ id: "root", ready: true }),
}));

const manifest = {
  slug: "rca",
  title: "RCA",
  icon: "flame",
  color: "#000",
  function: { workspace: true, sandbox: false, terminal: true },
  agent: { picker: [] },
  item: { noun: "Investigation", noun_plural: "Investigations" },
  layout: {
    breadcrumb: [],
    statusbar: [],
    list: [],
    default_tabs: [],
    primary_surface: "ide",
    chat_switcher: false,
  },
  labels: {},
  fields: [],
  field_styles: {},
  profiles: [],
  default_profile: "default",
  resource_route: "/rca-investigation",
} as unknown as AppManifest;

const item = {
  resource_id: "INC-1",
  title: "Reflow drift",
  owner: "root",
  created_by: "root",
  permission: { visibility: "private" },
} as unknown as AppItem;

function openShell(files: { path: string; size: number }[] = [], m: AppManifest = manifest) {
  return renderWithQuery(
    <MemoryRouter>
      <WorkspaceShell manifest={m} item={item} files={files as never} />
    </MemoryRouter>,
  );
}

afterEach(() => cleanup());

// plan-wui-publish-and-agent-hub A11: the chat of an agent's copy is one
// conversation; any other item's is not.
describe("WorkspaceShell chat of an agent's copy", () => {
  it("is one conversation", async () => {
    renderWithQuery(
      <MemoryRouter>
        <WorkspaceShell manifest={manifest} item={{ ...item, published_kind: "agent" } as AppItem} files={[]} />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId("chat")).toHaveAttribute("data-one", "true");
  });

  it("an ordinary item's is not", async () => {
    openShell();

    expect(await screen.findByTestId("chat")).toHaveAttribute("data-one", "false");
  });

  it("puts its controls in the ONE top bar — the way back, a new conversation, the history", async () => {
    // The user, 2026-11-05: 「兩個 header 不行」.
    renderWithQuery(
      <MemoryRouter>
        <WorkspaceShell
          manifest={manifest}
          item={{ ...item, published_kind: "agent", published_from: "src∕a∕agent.ai.yaml" } as AppItem}
          files={[]}
        />
      </MemoryRouter>,
    );
    const bar = await screen.findByTestId("topbar");

    expect(within(bar).getByRole("link", { name: /回到 Agent Hub/ })).toBeInTheDocument();
    expect(within(bar).getByRole("link", { name: "新對話" })).toBeInTheDocument();
    expect(within(bar).getByRole("link", { name: "歷史對話" })).toBeInTheDocument();
    expect(screen.getAllByTestId("topbar")).toHaveLength(1);
  });

  it("an ordinary item's top bar has none of them", async () => {
    openShell();
    const bar = await screen.findByTestId("topbar");

    expect(within(bar).queryByRole("link", { name: /回到 Agent Hub/ })).toBeNull();
    expect(within(bar).queryByRole("link", { name: "新對話" })).toBeNull();
  });
});
