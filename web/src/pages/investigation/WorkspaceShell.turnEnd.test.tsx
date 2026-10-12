// @vitest-environment happy-dom
/**
 * A turn's end tells the page too (`onFilesChanged`): the item's file list and
 * its usage bar (#538) re-read on it. The shell hands that callback to
 * `WorkspaceProviders`, which runs it after the files reload.
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import { publishTurnEnded } from "../../lib/turnEndedBus";
import { renderWithQuery } from "../../test/queryWrapper";
import { WorkspaceShell } from "./WorkspaceShell";

vi.mock("../../components/ItemChatShell", () => ({ ItemChatShell: () => null }));
vi.mock("../../components/PresenceBar", () => ({ PresenceBar: () => null }));
vi.mock("../../components/ActivityFeed", () => ({ ActivityFeed: () => null }));
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
  slug: "playground",
  title: "Playground",
  icon: "sparkle",
  color: "#000",
  function: { workspace: true, sandbox: false, terminal: false },
  agent: { picker: [] },
  item: { noun: "Scratch", noun_plural: "Scratches" },
  layout: {
    breadcrumb: [],
    statusbar: [],
    list: [],
    default_tabs: [],
    primary_surface: "chat",
    chat_switcher: false,
  },
  labels: {},
  fields: [],
  field_styles: {},
  profiles: [],
  default_profile: "default",
  resource_route: "/playground-item",
} as unknown as AppManifest;

const item = {
  resource_id: "PG-1",
  title: "Sine wave demo",
  owner: "root",
  created_by: "root",
  permission: { visibility: "private" },
} as unknown as AppItem;

afterEach(() => {
  localStorage.clear();
  cleanup();
});

describe("WorkspaceShell — a turn's end", () => {
  it("tells the page its files changed", async () => {
    const onFilesChanged = vi.fn();
    renderWithQuery(
      <MemoryRouter>
        <WorkspaceShell manifest={manifest} item={item} files={[]} onFilesChanged={onFilesChanged} />
      </MemoryRouter>,
    );

    act(() => publishTurnEnded("PG-1"));

    await waitFor(() => expect(onFilesChanged).toHaveBeenCalledTimes(1));
  });
});
