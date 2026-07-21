// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import { renderWithQuery } from "../../test/queryWrapper";
import { WorkspaceShell } from "./WorkspaceShell";

// This test is about ONE decision — whether the IDE column renders — so every
// heavy child (live chat SSE, presence, activity feed, the file service) is
// stubbed down to a marker. `ActivityBar`, the thing we assert on, is internal
// to WorkspaceShell and only mounts inside the `read_content` branch.
const chatReadOnly = vi.fn();
vi.mock("../../components/ItemChatShell", () => ({
  ItemChatShell: ({ readOnly }: { readOnly?: boolean }) => {
    chatReadOnly(readOnly);
    return <div data-testid="chat" />;
  },
}));
vi.mock("../../components/PresenceBar", () => ({ PresenceBar: () => null }));
vi.mock("../../components/ActivityFeed", () => ({ ActivityFeed: () => null }));
vi.mock("../../hooks/useAgent", () => ({
  AgentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAgent: () => ({ log: { entries: [], streaming: false }, metrics: null }),
}));

const isSuperuser = vi.fn(() => false);
vi.mock("../../hooks/useIsSuperuser", () => ({ useIsSuperuser: () => isSuperuser() }));
vi.mock("../../hooks/useCurrentUser", () => ({ useCurrentUser: () => "root" }));

const manifest = {
  slug: "rca",
  title: "RCA",
  icon: "flame",
  color: "#000",
  function: { workspace: true, sandbox: false, terminal: false },
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

/** Someone else's private item — visible to an admin, owned by alice. */
const item = {
  resource_id: "INC-1",
  title: "Reflow drift",
  owner: "alice",
  created_by: "alice",
  permission: { visibility: "private" },
} as unknown as AppItem;

function open() {
  return renderWithQuery(
    <MemoryRouter>
      <WorkspaceShell manifest={manifest} item={item} files={[]} />
    </MemoryRouter>,
  );
}

beforeEach(() => isSuperuser.mockReturnValue(false));
afterEach(cleanup);

// The reported bug: an admin could see the work item in the list and open it,
// and then the workspace was simply not there — no activity bar, no file tree,
// no error. `read_content` was decided from the user id alone, so the admin fell
// into the `visibility === "private"` branch that the backend never applies to
// them.
describe("WorkspaceShell — who gets the IDE column", () => {
  it("renders the workspace for a superuser on someone else's private item", async () => {
    isSuperuser.mockReturnValue(true);
    open();
    expect(await screen.findByTitle("Search files")).toBeInTheDocument();
    // The other half of the symptom: the composer was read-only too.
    expect(chatReadOnly).toHaveBeenLastCalledWith(false);
  });

  it("still hides it from a plain non-owner with no read_content", async () => {
    open();
    await waitFor(() => expect(screen.getByTestId("page-item")).toBeInTheDocument());
    expect(screen.queryByTitle("Search files")).not.toBeInTheDocument();
    expect(chatReadOnly).toHaveBeenLastCalledWith(true);
  });
});
