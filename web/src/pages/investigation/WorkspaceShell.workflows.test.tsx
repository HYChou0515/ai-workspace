// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import { workflowApi, type WorkflowRunDTO } from "../../api/workflows";
import { DialogProvider } from "../../components/Dialog";
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
  WorkflowsSidebar: (p: { profile: string; itemId: string; onOpenFile: (path: string) => void }) => (
    <div data-testid="workflows-sidebar" data-profile={p.profile} data-item={p.itemId}>
      <button type="button" onClick={() => p.onOpenFile("workflow-run:r1")}>
        open execution r1
      </button>
    </div>
  ),
}));
vi.mock("../../components/ExecutionView", () => ({
  ExecutionView: (p: { runId: string; profile: string; itemId: string }) => (
    <div data-testid="execution-view" data-run={p.runId} data-profile={p.profile} data-item={p.itemId} />
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
      run("workflow-run:a", "awaiting_human"),
      run("workflow-run:b", "running"),
      run("workflow-run:c", "awaiting_human"),
      run("workflow-run:d", "done"),
    ]);
    openShell();
    const badge = await screen.findByTestId("workflows-waiting");
    expect(badge).toHaveTextContent("2");
    expect(screen.getByRole("button", { name: /^Workflows/ })).toHaveAccessibleName(
      "Workflows, 2 waiting for you",
    );
  });

  it("carries no badge when nothing is waiting", async () => {
    vi.mocked(workflowApi.listRuns).mockResolvedValue([run("workflow-run:b", "running")]);
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

// D7: an execution opens as an editor tab — not a file, so the rules for files
// (closed when the listing lacks it, kept in Recently opened) do not apply.
describe("an execution's tab", () => {
  const openExecution = async () => {
    openShell();
    fireEvent.click(await screen.findByRole("button", { name: /^Workflows/ }), { detail: 1 });
    fireEvent.click(await screen.findByRole("button", { name: "open execution r1" }));
  };

  beforeEach(() => {
    vi.spyOn(workflowApi, "getRun").mockResolvedValue({
      ...run("workflow-run:r1", "running"),
      started: new Date(2026, 9, 12, 14, 3).getTime(),
    });
    vi.spyOn(workflowApi, "listProfiles").mockResolvedValue([
      {
        name: "default",
        title: "Default",
        description: "",
        has_workflow: true,
        workflow: null,
        workflows: [{ id: "w", title: "Classify wafers", phases: [], input_json: "" }],
      },
    ]);
  });

  it("shows the execution for this item and its profile", async () => {
    await openExecution();
    const view = await screen.findByTestId("execution-view");
    expect(view).toHaveAttribute("data-run", "workflow-run:r1");
    expect(view).toHaveAttribute("data-item", "INC-1");
    expect(view).toHaveAttribute("data-profile", "default");
    // the status bar's file facts are not an execution's (found live: "lang: execution")
    expect(screen.queryByText(/^lang: /)).toBeNull();
    expect(screen.queryByText("UTF-8")).toBeNull();
  });

  it("is named after the execution, not its path", async () => {
    await openExecution();
    const tab = await screen.findByRole("tab", { name: /Classify wafers · 10\/12 14:03/ });
    expect(tab).not.toHaveTextContent("workflow-run:");
  });

  it("stays open when the file listing comes back without its path", async () => {
    // A fresh listing is what closes a tab whose file is gone; the tab was
    // opened under the first one, so only the second is the test.
    const { rerender, client } = openShell();
    fireEvent.click(await screen.findByRole("button", { name: /^Workflows/ }), { detail: 1 });
    fireEvent.click(await screen.findByRole("button", { name: "open execution r1" }));
    await screen.findByTestId("execution-view");
    rerender(
      <QueryClientProvider client={client}>
        <DialogProvider>
          <MemoryRouter>
            <WorkspaceShell manifest={manifest} item={item} files={[{ path: "/a.md", size: 1 }] as never} />
          </MemoryRouter>
        </DialogProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByRole("tab", { name: /Classify wafers/ })).toBeInTheDocument());
    expect(screen.getByTestId("execution-view")).toBeInTheDocument();
  });

  it("does not take a place in Recently opened", async () => {
    // History keeps the last ten files; an execution would push one out.
    await openExecution();
    await screen.findByTestId("execution-view");
    expect(localStorage.getItem("rca:recent-files:INC-1") ?? "").not.toContain("workflow-run:");
  });
});

// P9: a waiting execution is announced wherever the person is in the workspace.
describe("the waiting notice", () => {
  it("announces an execution waiting at a gate and opens its tab", async () => {
    vi.mocked(workflowApi.listRuns).mockResolvedValue([
      {
        ...run("workflow-run:r1", "awaiting_human"),
        pending_decision: { phase: "p", title: "Check it", summary: "", allow: ["approve"], decided_by: "" },
      },
    ]);
    vi.spyOn(workflowApi, "getRun").mockResolvedValue(run("workflow-run:r1", "awaiting_human"));
    openShell();
    const notice = await screen.findByTestId("waiting-notice");
    expect(notice).toHaveTextContent("Check it");
    fireEvent.click(within(notice).getByRole("button", { name: "Open" }));
    expect((await screen.findByTestId("execution-view")).dataset.run).toBe("workflow-run:r1");
    // now in front of the person: the notice has done its job
    await waitFor(() => expect(screen.queryByTestId("waiting-notice")).toBeNull());
  });
});

// D8 / D9: an execution asks for a chat — a folded chat unfolds to show it.
describe("a chat asked for from an execution", () => {
  it("unfolds the chat", async () => {
    const { publishChatRequest } = await import("../../lib/chatRequestBus");
    localStorage.setItem("rca:layout:chat-collapsed", "true");
    openShell();
    expect(await screen.findByRole("button", { name: "Show chat" })).toBeInTheDocument();
    act(() => publishChatRequest("INC-1", "Why did it fail?"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Show chat" })).toBeNull());
  });
});

// P11: the schedules overview links an execution as `?open=workflow-run:<id>`.
describe("an address that names an execution", () => {
  afterEach(() => window.history.replaceState(null, "", "/"));

  it("opens that execution's tab, the workspace unfolded", async () => {
    vi.spyOn(workflowApi, "getRun").mockResolvedValue(run("workflow-run:r7", "done"));
    window.history.replaceState(null, "", "/?open=workflow-run%3Ar7");
    const chatFirst = {
      ...manifest,
      layout: { ...manifest.layout, primary_surface: "chat" },
    } as unknown as AppManifest;
    openShell(chatFirst);
    expect((await screen.findByTestId("execution-view")).dataset.run).toBe("workflow-run:r7");
  });

  it("opens no file by it — only an execution", async () => {
    window.history.replaceState(null, "", "/?open=%2Fsecret.md");
    openShell();
    await screen.findByRole("button", { name: /^Workflows/ });
    expect(screen.queryByRole("tab", { name: /secret/ })).toBeNull();
  });
});

describe("the badge (review round 3)", () => {
  it("does not count an execution superseded by a newer one: it can only be stopped", async () => {
    vi.mocked(workflowApi.listRuns).mockResolvedValue([
      { ...run("workflow-run:a", "awaiting_human"), latest: false },
      { ...run("workflow-run:b", "awaiting_human"), latest: true },
    ]);
    openShell();
    expect(await screen.findByTestId("workflows-waiting")).toHaveTextContent("1");
  });
});
