// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { workflowApi, type ProfileDTO, type WorkflowRunDTO } from "../api/workflows";
import { workspaceWorkflowsApi } from "../api/workspaceWorkflows";
import { QueryWrap } from "../test/queryWrapper";
import { runsPollMs } from "./useWorkflow";
import { useLaunchableWorkflows } from "./useLaunchableWorkflows";

afterEach(() => vi.restoreAllMocks());

const PROFILES: ProfileDTO[] = [
  {
    name: "default",
    title: "Default",
    description: "",
    has_workflow: true,
    workflow: null,
    workflows: [
      { id: "memory", title: "Digest uploads into memory", phases: [], input_json: "x" },
      { id: "shared", title: "Package version", phases: [], input_json: "x" },
    ],
  },
];

// The rule moved here from the chat's New picker (plan-workflow-out-of-chat P6):
// the Workflows sidebar is now where a workflow is started.
describe("useLaunchableWorkflows", () => {
  it("offers the profile's workflows and the workspace's, a workspace one shadowing its package twin", async () => {
    vi.spyOn(workflowApi, "listProfiles").mockResolvedValue(PROFILES);
    vi.spyOn(workspaceWorkflowsApi, "list").mockResolvedValue([
      { id: "shared", title: "My edited version", phases: [] },
      { id: "mine", title: "Written here", phases: [] },
    ]);
    const { result } = renderHook(() => useLaunchableWorkflows("hub", "it", "default"), {
      wrapper: QueryWrap,
    });
    await waitFor(() => expect(result.current).toHaveLength(3));
    expect(Object.fromEntries(result.current.map((w) => [w.id, w.title]))).toEqual({
      memory: "Digest uploads into memory",
      shared: "My edited version",
      mine: "Written here",
    });
  });

  it("does not offer a workspace workflow that will not parse", async () => {
    // The Workflows panel lists it with its reason and no Run; offering it here
    // reached the pre-flight and came back "no such workflow".
    vi.spyOn(workflowApi, "listProfiles").mockResolvedValue([]);
    vi.spyOn(workspaceWorkflowsApi, "list").mockResolvedValue([
      { id: "good", title: "Good one", phases: [{ id: "a", title: "A" }] },
      { id: "broken", title: "broken", phases: [], problem: "steps[0]: `cache` is required" },
    ]);
    const { result } = renderHook(() => useLaunchableWorkflows("hub", "it", "default"), {
      wrapper: QueryWrap,
    });
    await waitFor(() => expect(result.current.map((w) => w.id)).toEqual(["good"]));
  });
});

const run = (status: WorkflowRunDTO["status"]): WorkflowRunDTO => ({
  run_id: status,
  item_id: "it",
  captured_user: "u",
  status,
  current_phase: "",
  phases: [],
  steps: [],
  failures: [],
  started: 1,
  ended: null,
  result: null,
  pending_decision: null,
});

describe("runsPollMs", () => {
  it("keeps the list fresh while any execution is still going, so a gate's badge appears", () => {
    expect(runsPollMs([run("done"), run("running")])).toBeGreaterThan(0);
    expect(runsPollMs([run("awaiting_human")])).toBeGreaterThan(0);
    expect(runsPollMs([run("pending")])).toBeGreaterThan(0);
  });

  it("stops once every execution has ended", () => {
    expect(runsPollMs([run("done"), run("error"), run("cancelled")])).toBe(false);
    expect(runsPollMs([])).toBe(false);
    expect(runsPollMs(undefined)).toBe(false);
  });
});
