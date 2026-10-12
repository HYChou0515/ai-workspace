// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FileService } from "../api/fileService";
import { workflowApi, type ProfileDTO, type WorkflowRunDTO } from "../api/workflows";
import { workspaceWorkflowsApi } from "../api/workspaceWorkflows";
import { executionName, executionPath } from "../lib/executionTab";
import { renderWithQuery } from "../test/queryWrapper";
import { DialogProvider } from "./Dialog";
import { WorkflowsSidebar } from "./WorkflowsSidebar";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const PROFILES: ProfileDTO[] = [
  {
    name: "default",
    title: "Default",
    description: "",
    has_workflow: true,
    workflow: null,
    workflows: [
      {
        id: "classify",
        title: "Classify wafers",
        phases: [
          { id: "read", title: "Read" },
          { id: "sort", title: "Sort" },
          { id: "write", title: "Write" },
        ],
        input_json: "x",
      },
    ],
  },
];

const run = (over: Partial<WorkflowRunDTO>): WorkflowRunDTO => ({
  run_id: "workflow-run:r1",
  item_id: "it",
  workflow_id: "classify",
  captured_user: "u",
  status: "done",
  current_phase: "",
  phases: [],
  steps: [],
  failures: [],
  started: 1_000,
  ended: 2_000,
  result: null,
  pending_decision: null,
  ...over,
});

beforeEach(() => {
  vi.spyOn(workflowApi, "listProfiles").mockResolvedValue(PROFILES);
  vi.spyOn(workspaceWorkflowsApi, "list").mockResolvedValue([]);
  vi.spyOn(workflowApi, "listRuns").mockResolvedValue([]);
});

const render = (onOpenFile = vi.fn()) => {
  renderWithQuery(
    <DialogProvider>
      <WorkflowsSidebar
        slug="hub"
        itemId="it"
        profile="default"
        fileService={{} as FileService}
        onOpenFile={onOpenFile}
      />
    </DialogProvider>,
  );
  return onOpenFile;
};

// plan-workflow-out-of-chat D7: workflows are started and followed here, in the
// workspace, not in a chat.
describe("WorkflowsSidebar", () => {
  it("starts a workflow after its pre-flight, then opens the execution's tab", async () => {
    vi.spyOn(workflowApi, "previewRun").mockResolvedValue({
      workflow_id: "classify",
      title: "Classify wafers",
      description: "",
      phases: [],
      summary: "",
      checks: [],
      can_run: true,
      has_preflight: false,
    });
    const start = vi
      .spyOn(workflowApi, "startRun")
      .mockResolvedValue({ run_id: "workflow-run:r9", item_id: "it", chat_id: "conversation:x" });
    const onOpenFile = render();

    fireEvent.click(await screen.findByRole("button", { name: "Start Classify wafers" }));
    await screen.findByTestId("wf-launch-dialog");
    expect(start).not.toHaveBeenCalled(); // nothing starts before the pre-flight is confirmed
    const go = await screen.findByTestId("wf-launch-run");
    await waitFor(() => expect(go).toBeEnabled());
    fireEvent.click(go);

    await waitFor(() => expect(start).toHaveBeenCalledWith("hub", "it", "classify"));
    await waitFor(() => expect(onOpenFile).toHaveBeenCalledWith(executionPath("workflow-run:r9")));
  });

  it("says why a start failed instead of going quiet", async () => {
    vi.spyOn(workflowApi, "previewRun").mockResolvedValue({
      workflow_id: "classify",
      title: "Classify wafers",
      description: "",
      phases: [],
      summary: "",
      checks: [],
      can_run: true,
      has_preflight: false,
    });
    vi.spyOn(workflowApi, "startRun").mockRejectedValue(new Error("run failed: 409"));
    render();

    fireEvent.click(await screen.findByRole("button", { name: "Start Classify wafers" }));
    const go = await screen.findByTestId("wf-launch-run");
    await waitFor(() => expect(go).toBeEnabled()); // the pre-flight has come back
    fireEvent.click(go);

    expect(await screen.findByRole("alert")).toHaveTextContent("run failed: 409");
  });

  it("lists executions newest first, each saying how far it got, and opens one as a tab", async () => {
    vi.mocked(workflowApi.listRuns).mockResolvedValue([
      run({ run_id: "workflow-run:old", started: 1_000, status: "done" }),
      run({
        run_id: "workflow-run:new",
        started: 5_000,
        status: "running",
        current_phase: "sort",
        phases: [
          { phase: "read", status: "passed", done: 1, total: 1, failed: 0 },
          { phase: "sort", status: "running", done: 0, total: 1, failed: 0 },
        ],
        schedule_key: "wui:it:nightly",
      }),
    ]);
    const onOpenFile = render();

    const rows = await screen.findAllByTestId("execution-row");
    expect(rows.map((r) => r.getAttribute("data-run"))).toEqual(["workflow-run:new", "workflow-run:old"]);
    // named as its tab is: the workflow and when it started
    expect(within(rows[0]).getByText(executionName("Classify wafers", 5_000))).toBeInTheDocument();
    expect(within(rows[0]).getByText("Step 2 of 3")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Running")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Scheduled")).toBeInTheDocument();
    expect(within(rows[1]).queryByText("Scheduled")).toBeNull();
    expect(within(rows[1]).getByText("Done")).toBeInTheDocument();
    // found live: a finished execution read "Done · Step 1 of 2" when a phase its
    // path never needed stayed not-started; "Done" says how far it got
    expect(within(rows[1]).queryByText(/^Step /)).toBeNull();

    fireEvent.click(rows[1]);
    expect(onOpenFile).toHaveBeenCalledWith(executionPath("workflow-run:old"));
  });

  it("opens the Workflows panel to manage the workspace's own workflows", async () => {
    render();
    fireEvent.click(await screen.findByRole("button", { name: "Manage workflows…" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});
