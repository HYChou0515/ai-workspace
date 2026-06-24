import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchChatExport,
  fmtElapsed,
  isRunTerminal,
  phaseView,
  stepBoard,
  stepElapsedMs,
  type PhaseDef,
  type StepStateDTO,
  type WorkflowRunDTO,
} from "./workflows";

function step(over: Partial<StepStateDTO> = {}): StepStateDTO {
  return {
    phase: "commit",
    name: "ingest",
    key: "",
    status: "running",
    attempts: 1,
    reason: "",
    started: 1000,
    ended: null,
    ...over,
  };
}

const DECLARED: PhaseDef[] = [
  { id: "classify", title: "Classify" },
  { id: "review", title: "Review" },
  { id: "ingest", title: "Ingest" },
];

function run(over: Partial<WorkflowRunDTO> = {}): WorkflowRunDTO {
  return {
    run_id: "r1",
    item_id: "i1",
    captured_user: "u",
    status: "running",
    current_phase: "",
    phases: [],
    steps: [],
    failures: [],
    started: 1,
    ended: null,
    result: null,
    pending_decision: null,
    ...over,
  };
}

describe("phaseView", () => {
  it("renders the manifest skeleton as pending when there is no run", () => {
    const nodes = phaseView(DECLARED, null);
    expect(nodes.map((n) => n.id)).toEqual(["classify", "review", "ingest"]);
    expect(nodes.every((n) => n.status === "pending")).toBe(true);
    expect(nodes[0].title).toBe("Classify");
  });

  it("overlays the run's per-phase progress + marks the current phase", () => {
    const nodes = phaseView(
      DECLARED,
      run({
        current_phase: "classify",
        phases: [
          { phase: "classify", status: "running", done: 3, total: 5, failed: 1 },
          { phase: "review", status: "pending", done: 0, total: 0, failed: 0 },
        ],
      }),
    );
    const classify = nodes.find((n) => n.id === "classify")!;
    expect(classify.status).toBe("running");
    expect(classify.done).toBe(3);
    expect(classify.failed).toBe(1);
    expect(classify.current).toBe(true);
  });

  it("shows the gated phase as awaiting_human", () => {
    const nodes = phaseView(
      DECLARED,
      run({
        status: "awaiting_human",
        current_phase: "review",
        pending_decision: { phase: "review", title: "ok?", summary: "", allow: [], decided_by: "" },
      }),
    );
    expect(nodes.find((n) => n.id === "review")!.status).toBe("awaiting_human");
  });

  it("appends a phase the run touched that the manifest didn't declare (drift)", () => {
    const nodes = phaseView(
      DECLARED,
      run({ phases: [{ phase: "surprise", status: "passed", done: 1, total: 1, failed: 0 }] }),
    );
    expect(nodes.map((n) => n.id)).toContain("surprise");
    expect(nodes.find((n) => n.id === "surprise")!.status).toBe("passed");
  });
});

describe("isRunTerminal", () => {
  it("treats done/error/cancelled as terminal and the rest as live", () => {
    expect(isRunTerminal("done")).toBe(true);
    expect(isRunTerminal("error")).toBe(true);
    expect(isRunTerminal("cancelled")).toBe(true);
    expect(isRunTerminal("running")).toBe(false);
    expect(isRunTerminal("awaiting_human")).toBe(false);
    expect(isRunTerminal("pending")).toBe(false);
  });
});

describe("fetchChatExport (#100 — export fail-loud)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("hits the app-scoped export route, not the removed /investigations one", async () => {
    const fetchMock = vi.fn(
      async (_url: string): Promise<Response> =>
        new Response('{"title":"x","messages":[]}', {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await fetchChatExport("topic-hub", "topic-hub:1");
    const url = fetchMock.mock.calls[0][0];
    expect(url).toContain("/a/topic-hub/items/topic-hub%3A1/export-chat");
    expect(url).not.toContain("/investigations/");
  });

  it("throws (no silent HTML download) when the response is the SPA shell, not the chat", async () => {
    // The old bug: a misrouted GET falls through to the SPA → text/html 200,
    // which the browser saved as export-chat.html. Now it's a loud error.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response("<!doctype html><html></html>", {
          status: 200,
          headers: { "content-type": "text/html; charset=utf-8" },
        }),
      ),
    );
    await expect(fetchChatExport("topic-hub", "topic-hub:1")).rejects.toThrow(/匯出/);
  });
});

describe("stepBoard (#178)", () => {
  it("groups steps under their phase node, preserving diagram order", () => {
    const nodes = phaseView(DECLARED, run());
    const steps = [
      step({ phase: "classify", name: "classify_a" }),
      step({ phase: "ingest", name: "ingest", key: "a.md" }),
      step({ phase: "ingest", name: "ingest", key: "b.md" }),
    ];
    const board = stepBoard(nodes, steps);
    expect(board.map((g) => g.node.id)).toEqual(["classify", "review", "ingest"]);
    expect(board.find((g) => g.node.id === "classify")!.steps).toHaveLength(1);
    expect(board.find((g) => g.node.id === "review")!.steps).toHaveLength(0);
    expect(board.find((g) => g.node.id === "ingest")!.steps).toHaveLength(2);
  });
});

describe("stepElapsedMs / fmtElapsed (#178)", () => {
  it("a running step keeps ticking (now - started)", () => {
    expect(stepElapsedMs(step({ started: 1000, ended: null }), 4500)).toBe(3500);
  });

  it("a finished step freezes at ended - started", () => {
    expect(stepElapsedMs(step({ started: 1000, ended: 9000 }), 999999)).toBe(8000);
  });

  it("a step with no start (cache skip) has no timer", () => {
    expect(stepElapsedMs(step({ started: null }), 5000)).toBeNull();
  });

  it("formats as m:ss and h:mm:ss", () => {
    expect(fmtElapsed(42_000)).toBe("0:42");
    expect(fmtElapsed(272_000)).toBe("4:32");
    expect(fmtElapsed(3_661_000)).toBe("1:01:01");
  });
});
