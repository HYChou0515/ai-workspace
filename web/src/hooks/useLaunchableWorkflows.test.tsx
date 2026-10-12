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
    expect(runsPollMs([run("pending")])).toBeGreaterThan(0);
  });

  it("waits at the slow pace while an execution only waits at a gate", () => {
    // Review R1: a gate may wait for days; every open tab polling every 2 s for it
    // is load, not news — the badge is already up.
    expect(runsPollMs([run("awaiting_human")])).toBe(runsPollMs([]));
  });

  it("slows down, but never stops, once every execution has ended", () => {
    // Review R1: a run a schedule, a page or another person starts later must still
    // reach the badge and the notice — nothing else refetches the list.
    const live = runsPollMs([run("running")]);
    for (const idle of [runsPollMs([run("done"), run("error"), run("cancelled")]), runsPollMs([])]) {
      expect(idle).toBeGreaterThan(live as number);
      expect(idle).toBeLessThanOrEqual(60_000);
    }
  });
});

describe("useRun", () => {
  it("stops asking for a run it cannot read", async () => {
    // Review R1: a pruned run or a bad link was asked for every second for as
    // long as its tab stayed open.
    const { renderHook, waitFor } = await import("@testing-library/react");
    const { useRun } = await import("./useWorkflow");
    const get = vi.spyOn(workflowApi, "getRun").mockRejectedValue(new Error("unknown run"));
    const { result } = renderHook(() => useRun("hub", "it", "workflow-run:gone"), {
      wrapper: QueryWrap,
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    const asked = get.mock.calls.length;
    await new Promise((r) => setTimeout(r, 2500));
    expect(get.mock.calls.length).toBe(asked);
  });
});
