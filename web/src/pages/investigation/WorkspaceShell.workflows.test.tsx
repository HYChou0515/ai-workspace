// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import { workflowApi, type WorkflowRunDTO } from "../../api/workflows";
import { renderWithQuery } from "../../test/queryWrapper";
import { WorkspaceShell } from "./WorkspaceShell";

// Everything live is stubbed to a marker — these tests are about ONE thing:
// which shell panels are showing, and what opens or retracts them.
vi.mock("../../components/ItemChatShell", () => ({
  ItemChatShell: () => <div data-testid="chat" />,
}));
vi.mock("../../components/PresenceBar", () => ({ PresenceBar: () => null }));
vi.mock("../../components/ActivityFeed", () => ({ ActivityFeed: () => null }));
vi.mock("./TerminalPane", () => ({ TerminalPane: () => null }));
vi.mock("../../components/WorkflowsSidebar", () => ({
  WorkflowsSidebar: (p: { profile: string; itemId: string }) => (
    <div data-testid="workflows-sidebar" data-profile={p.profile} data-item={p.itemId} />
  ),
}));
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

function openShell(m: AppManifest = manifest, it: AppItem = item) {
  return renderWithQuery(
    <MemoryRouter>
      <WorkspaceShell manifest={m} item={it} files={[] as never} />
    </MemoryRouter>,
  );
}

const run = (run_id: string, status: WorkflowRunDTO["status"]) =>
  ({ run_id, item_id: "INC-1", workflow_id: "w", status, phases: [], steps: [], failures: [] }) as unknown as WorkflowRunDTO;

beforeEach(() => {
  vi.spyOn(workflowApi, "listRuns").mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
});

// plan-workflow-out-of-chat D7: a labelled activity-bar entry (NN/g: an icon
// needs a visible label) with a badge of executions waiting for a person.
describe("the Workflows activity-bar entry", () => {
  it("shows its name, not only a tooltip", async () => {
    openShell();
    const entry = await screen.findByRole("button", { name: /^Workflows/ });
    expect(entry).toHaveTextContent("Workflows");
  });

  it("counts the executions waiting for you, and only those", async () => {
    vi.mocked(workflowApi.listRuns).mockResolvedValue([
      run("a", "awaiting_human"),
      run("b", "running"),
      run("c", "awaiting_human"),
      run("d", "done"),
    ]);
    openShell();
    const badge = await screen.findByTestId("workflows-waiting");
    expect(badge).toHaveTextContent("2");
    expect(screen.getByRole("button", { name: /^Workflows/ })).toHaveAccessibleName(
      "Workflows, 2 waiting for you",
    );
  });

  it("carries no badge when nothing is waiting", async () => {
    vi.mocked(workflowApi.listRuns).mockResolvedValue([run("b", "running")]);
    openShell();
    await screen.findByRole("button", { name: /^Workflows/ });
    await waitFor(() => expect(workflowApi.listRuns).toHaveBeenCalled());
    expect(screen.queryByTestId("workflows-waiting")).toBeNull();
  });

  it("opens the Workflows sidebar for this item and its profile", async () => {
    openShell(manifest, { ...item, profile: "fast" } as unknown as AppItem);
    fireEvent.click(await screen.findByRole("button", { name: /^Workflows/ }), { detail: 1 });
    const pane = await screen.findByTestId("workflows-sidebar");
    expect(pane).toHaveAttribute("data-item", "INC-1");
    expect(pane).toHaveAttribute("data-profile", "fast");
  });

  it("falls back to the App's default profile", async () => {
    openShell();
    fireEvent.click(await screen.findByRole("button", { name: /^Workflows/ }), { detail: 1 });
    expect(await screen.findByTestId("workflows-sidebar")).toHaveAttribute("data-profile", "default");
  });
});
