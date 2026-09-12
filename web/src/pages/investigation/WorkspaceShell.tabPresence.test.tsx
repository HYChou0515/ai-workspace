// @vitest-environment happy-dom
/**
 * "Not in the listing" no longer means "gone". The tree preloads a PRUNED
 * workspace — `node_modules/` and friends are listed but not entered — so a
 * file under one of those folders is absent from `files` while very much on
 * disk. The shell's auto-close of tabs whose file vanished must not fire on it.
 * Both directions are pinned: the unknown one survives, the truly missing one
 * still closes — otherwise the rule that keeps a stale tab from lingering has
 * quietly been switched off.
 */
import "@testing-library/jest-dom/vitest";
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest, FileInfo } from "../../api/types";
import { DialogProvider } from "../../components/Dialog";
import { makeTestQueryClient } from "../../test/queryWrapper";
import { WorkspaceShell } from "./WorkspaceShell";

vi.mock("../../components/ItemChatShell", () => ({ ItemChatShell: () => null }));
vi.mock("../../components/PresenceBar", () => ({ PresenceBar: () => null }));
vi.mock("../../components/ActivityFeed", () => ({ ActivityFeed: () => null }));
vi.mock("../../hooks/useAgent", () => ({
  AgentProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
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

function manifestWith(defaultTabs: string[]): AppManifest {
  return {
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
      default_tabs: defaultTabs,
      chat_switcher: false,
    },
    labels: {},
    fields: [],
    field_styles: {},
    profiles: [],
    default_profile: "default",
    resource_route: "/playground-item",
  } as unknown as AppManifest;
}

const item = {
  resource_id: "PG-1",
  title: "Sine wave demo",
  owner: "root",
  created_by: "root",
  permission: { visibility: "private" },
} as unknown as AppItem;

function openShell(defaultTabs: string[], files: FileInfo[], unwalked: string[]) {
  const client = makeTestQueryClient();
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <DialogProvider>
        <MemoryRouter>{children}</MemoryRouter>
      </DialogProvider>
    </QueryClientProvider>
  );
  const ui = (next: FileInfo[]) => (
    <WorkspaceShell manifest={manifestWith(defaultTabs)} item={item} files={next} unwalked={unwalked} />
  );
  const r = render(ui(files), { wrapper: Wrapper });
  return { relist: (next: FileInfo[]) => r.rerender(ui(next)) };
}

const tabNames = () => screen.getAllByRole("tab").map((t) => t.textContent ?? "");

afterEach(() => {
  localStorage.clear();
  cleanup();
});

describe("WorkspaceShell — tabs under a folder the listing did not enter", () => {
  it("keeps a tab whose file lives under an unwalked folder when the listing refreshes", async () => {
    const files = [{ path: "/src/a.py", size: 1 }];
    const { relist } = openShell(["/src/a.py", "/node_modules/x/y.js"], files, ["/node_modules"]);
    await waitFor(() => expect(tabNames().join(" ")).toContain("y.js"));

    // A turn ended, the preload refetched: same answer, new array identity.
    relist([...files]);

    await waitFor(() => expect(tabNames().join(" ")).toContain("a.py"));
    expect(tabNames().join(" ")).toContain("y.js");
  });

  it("still closes a tab whose file really disappeared from a walked folder", async () => {
    const { relist } = openShell(
      ["/src/a.py", "/src/b.py"],
      [
        { path: "/src/a.py", size: 1 },
        { path: "/src/b.py", size: 1 },
      ],
      ["/node_modules"],
    );
    await waitFor(() => expect(tabNames().join(" ")).toContain("b.py"));

    relist([{ path: "/src/a.py", size: 1 }]);

    await waitFor(() => expect(tabNames().join(" ")).not.toContain("b.py"));
    expect(tabNames().join(" ")).toContain("a.py");
  });
});
