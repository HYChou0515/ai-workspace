// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { workflowApi, type ProfileDTO, type WorkflowRunDTO } from "../api/workflows";
import { workspaceWorkflowsApi } from "../api/workspaceWorkflows";
import { executionPath } from "../lib/executionTab";
import { renderWithQuery } from "../test/queryWrapper";
import { WaitingNotice } from "./WaitingNotice";

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
    workflows: [{ id: "classify", title: "Classify wafers", phases: [], input_json: "" }],
  },
];

const at = (h: number, m: number) => new Date(2026, 9, 12, h, m).getTime();

const waiting = (run_id: string, started: number, over: Partial<WorkflowRunDTO> = {}) =>
  ({
    run_id,
    item_id: "it",
    workflow_id: "classify",
    captured_user: "u",
    status: "awaiting_human",
    current_phase: "sort",
    phases: [],
    steps: [],
    failures: [],
    started,
    ended: null,
    result: null,
    pending_decision: { phase: "sort", title: "Check the sort", summary: "", allow: ["approve"], decided_by: "" },
    ...over,
  }) as WorkflowRunDTO;

beforeEach(() => {
  vi.spyOn(workflowApi, "listProfiles").mockResolvedValue(PROFILES);
  vi.spyOn(workspaceWorkflowsApi, "list").mockResolvedValue([]);
  vi.spyOn(workflowApi, "listRuns").mockResolvedValue([]);
});

const render = (activePath: string | null = null, onOpen = vi.fn()) => {
  const r = renderWithQuery(
    <WaitingNotice slug="hub" itemId="it" profile="default" activePath={activePath} onOpen={onOpen} />,
  );
  return { onOpen, ...r };
};

// plan-workflow-out-of-chat P9 (D7): wherever the person is in the workspace,
// an execution that waits for them says so — without taking over the screen,
// and without a timer taking it away, since it asks for an action.
describe("WaitingNotice", () => {
  it("names the execution and what it waits for, and opens it", async () => {
    vi.mocked(workflowApi.listRuns).mockResolvedValue([waiting("r1", at(14, 3))]);
    const { onOpen } = render();
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("Classify wafers · 10/12 14:03 is waiting for you");
    expect(notice).toHaveTextContent("Check the sort");
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(onOpen).toHaveBeenCalledWith(executionPath("r1"));
  });

  it("says a proposed change waits to be confirmed", async () => {
    vi.mocked(workflowApi.listRuns).mockResolvedValue([
      waiting("r1", at(14, 3), {
        pending_decision: null,
        pending_steer: { instruction: "x", rationale: "", input_edits: [], invalidate: [], decided_by: "" },
      }),
    ]);
    render();
    expect(await screen.findByRole("status")).toHaveTextContent("A change to confirm");
  });

  it("speaks only of an execution that waits — a running one does not", async () => {
    // The running one is newer: picked if "waiting" meant "not finished".
    vi.mocked(workflowApi.listRuns).mockResolvedValue([
      waiting("going", at(15, 0), { status: "running", pending_decision: null }),
      waiting("r1", at(14, 3)),
    ]);
    render();
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("10/12 14:03");
    expect(notice).not.toHaveTextContent("15:00");
    expect(notice).not.toHaveTextContent("more waiting");
  });

  it("is not about the execution whose tab is in front", async () => {
    // The one in front is the newer; the notice speaks of the other.
    vi.mocked(workflowApi.listRuns).mockResolvedValue([
      waiting("front", at(15, 0)),
      waiting("other", at(14, 3)),
    ]);
    const { onOpen } = render(executionPath("front"));
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("10/12 14:03");
    expect(notice).not.toHaveTextContent("more waiting"); // nor counted as another
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(onOpen).toHaveBeenCalledWith(executionPath("other"));
  });

  it("goes when dismissed, and comes back for the next gate", async () => {
    vi.mocked(workflowApi.listRuns).mockResolvedValue([waiting("r1", at(14, 3))]);
    const { client } = render();
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("status")).toBeNull();

    vi.mocked(workflowApi.listRuns).mockResolvedValue([
      waiting("r1", at(14, 3), {
        pending_decision: { phase: "write", title: "Check the report", summary: "", allow: ["approve"], decided_by: "" },
      }),
    ]);
    await client.invalidateQueries();
    expect(await screen.findByRole("status")).toHaveTextContent("Check the report");
  });

  it("shows the newest, and how many more wait", async () => {
    vi.mocked(workflowApi.listRuns).mockResolvedValue([
      waiting("old", at(9, 0)),
      waiting("new", at(14, 3)),
    ]);
    const { onOpen } = render();
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("10/12 14:03");
    expect(notice).toHaveTextContent("1 more waiting");
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(onOpen).toHaveBeenCalledWith(executionPath("new"));
  });
});
