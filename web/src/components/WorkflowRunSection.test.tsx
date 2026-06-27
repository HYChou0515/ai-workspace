// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { workflowApi, type WorkflowRunDTO } from "../api/workflows";
import { renderWithQuery } from "../test/queryWrapper";
import { WorkflowRunSection } from "./WorkflowRunSection";

afterEach(() => {
  cleanup(); // unmount so the run-poll timer stops before the next test
  vi.restoreAllMocks();
});

const ECHO_WF = { id: "", title: "Echo", phases: [{ id: "think", title: "Think" }], input_json: "x" };
const PROFILES = [
  {
    name: "echo",
    title: "Echo",
    description: "",
    has_workflow: true,
    workflow: ECHO_WF,
    workflows: [ECHO_WF],
  },
  {
    name: "default",
    title: "Default",
    description: "",
    has_workflow: false,
    workflow: null,
    workflows: [],
  },
];

const run = (over: Partial<WorkflowRunDTO> = {}): WorkflowRunDTO => ({
  run_id: "r1",
  item_id: "i1",
  captured_user: "u",
  status: "running",
  current_phase: "think",
  phases: [{ phase: "think", status: "running", done: 0, total: 0, failed: 0 }],
  steps: [],
  failures: [],
  started: 1,
  ended: null,
  result: null,
  pending_decision: null,
  ...over,
});

describe("WorkflowRunSection", () => {
  it("renders nothing for a non-workflow profile", async () => {
    vi.spyOn(workflowApi, "listProfiles").mockResolvedValue(PROFILES);
    const { container } = renderWithQuery(
      <WorkflowRunSection slug="playground" itemId="i1" profile="default" />,
    );
    // give the profiles query a tick to resolve, then confirm still nothing
    await waitFor(() => expect(workflowApi.listProfiles).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="wf-run-section"]')).toBeNull();
  });

  it("Run opens the pre-flight dialog, then confirming starts the run", async () => {
    vi.spyOn(workflowApi, "listProfiles").mockResolvedValue(PROFILES);
    vi.spyOn(workflowApi, "listRuns").mockResolvedValue([]);
    vi.spyOn(workflowApi, "getRun").mockResolvedValue(run());
    vi.spyOn(workflowApi, "previewRun").mockResolvedValue({
      workflow_id: "",
      title: "Echo",
      description: "",
      phases: [{ id: "think", title: "Think" }],
      summary: "will acknowledge a note",
      checks: [],
      can_run: true,
      has_preflight: true,
    });
    const start = vi
      .spyOn(workflowApi, "startRun")
      .mockResolvedValue({ run_id: "r1", item_id: "i1", chat_id: "conversation:c1" });

    renderWithQuery(<WorkflowRunSection slug="playground" itemId="i1" profile="echo" />);
    fireEvent.click(await screen.findByTestId("wf-run-button"));
    // the dialog opens first — the run hasn't started yet
    await screen.findByTestId("wf-launch-dialog");
    expect(start).not.toHaveBeenCalled();
    // confirm runs it
    fireEvent.click(await screen.findByTestId("wf-launch-run"));
    await waitFor(() => expect(start).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId("wf-run-panel")).toBeInTheDocument());
  });

  it("lists past runs as a first-class, always-visible list and opens one on click", async () => {
    vi.spyOn(workflowApi, "listProfiles").mockResolvedValue(PROFILES);
    vi.spyOn(workflowApi, "listRuns").mockResolvedValue([
      run({ run_id: "r2", status: "done", started: 200, ended: 260 }),
      run({ run_id: "r1", status: "error", started: 100, ended: 130 }),
    ]);
    vi.spyOn(workflowApi, "getRun").mockResolvedValue(run({ run_id: "r2" }));
    renderWithQuery(<WorkflowRunSection slug="playground" itemId="i1" profile="echo" />);
    // the runs list is visible without expanding anything (not a <details>)
    const items = await screen.findAllByTestId("wf-run-list-item");
    expect(items).toHaveLength(2);
    // newest-first; selecting an older run opens its panel
    fireEvent.click(items[1]);
    await waitFor(() => expect(screen.getByTestId("wf-run-panel")).toBeInTheDocument());
  });

  it("disables Run while a run is already active", async () => {
    vi.spyOn(workflowApi, "listProfiles").mockResolvedValue(PROFILES);
    vi.spyOn(workflowApi, "listRuns").mockResolvedValue([run({ status: "running" })]);
    vi.spyOn(workflowApi, "getRun").mockResolvedValue(run());
    renderWithQuery(<WorkflowRunSection slug="playground" itemId="i1" profile="echo" />);
    const btn = await screen.findByTestId("wf-run-button");
    // the runs list loads async; once it shows an active run the button disables
    await waitFor(() => expect(btn).toBeDisabled());
  });
});
