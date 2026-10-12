// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { QueryClient } from "@tanstack/react-query";

import type { FileService } from "../api/fileService";
import { itemChatApi } from "../api/itemChats";
import { makeQueryClient } from "../api/queryClient";
import type { Message } from "../api/types";
import { workflowApi, type ProfileDTO, type WorkflowRunDTO } from "../api/workflows";
import { workspaceWorkflowsApi } from "../api/workspaceWorkflows";
import type { AgentEntry } from "../pages/investigation/agentLog";
import { renderWithQuery } from "../test/queryWrapper";
import { ExecutionView } from "./ExecutionView";

// What the run's live stream adds beyond the persisted thread (the reply of the
// step being worked on, before it is saved).
let liveTail: AgentEntry[] = [];
vi.mock("../hooks/useItemChat", async () => {
  const { logFromMessages } = await import("../pages/investigation/agentLog");
  return {
    useItemChat: () => ({
      log: {
        entries: [...logFromMessages(persisted).entries, ...liveTail],
        streaming: liveTail.length > 0,
      },
    }),
  };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  liveTail = [];
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

const STARTED = new Date(2026, 9, 12, 14, 3).getTime();

const run = (over: Partial<WorkflowRunDTO> = {}): WorkflowRunDTO =>
  ({
    run_id: "workflow-run:r1",
    item_id: "it",
    workflow_id: "classify",
    chat_id: "conversation:c1",
    captured_user: "u",
    status: "running",
    current_phase: "sort",
    phases: [
      { phase: "read", status: "passed", done: 1, total: 1, failed: 0 },
      { phase: "sort", status: "running", done: 0, total: 1, failed: 0 },
    ],
    steps: [
      { phase: "read", name: "read", key: "", status: "passed", attempts: 1, reason: "", started: 1, ended: 2 },
      { phase: "sort", name: "sort", key: "", status: "running", attempts: 1, reason: "", started: 3, ended: null },
    ],
    failures: [],
    started: STARTED,
    ended: null,
    result: null,
    pending_decision: null,
    step_files: {
      read: { result: "/.workflow/classify/step_read/main.json", transcript: "/.workflow/classify/step_read/main.transcript.md" },
      sort: { result: "/.workflow/classify/step_sort/main.json", transcript: "/.workflow/classify/step_sort/main.transcript.md" },
    },
    ...over,
  }) as WorkflowRunDTO;

const msg = (over: Partial<Message>): Message => ({ role: "assistant", content: "", ...over }) as Message;

let persisted: Message[] = [];

beforeEach(() => {
  persisted = [
    msg({ role: "user", content: "Read the wafers", run_id: "workflow-run:r1", step: "read", phase: "read", driven_by: "workflow" }),
    msg({ content: "Read 12 wafers", run_id: "workflow-run:r1", step: "read", phase: "read" }),
    msg({ role: "user", content: "Sort them by defect", run_id: "workflow-run:r1", step: "sort", phase: "sort", driven_by: "workflow" }),
    // another execution of a schedule that shares this conversation
    msg({ content: "From an older execution", run_id: "workflow-run:r0", step: "read", phase: "read" }),
  ];
  vi.spyOn(workflowApi, "listProfiles").mockResolvedValue(PROFILES);
  vi.spyOn(workspaceWorkflowsApi, "list").mockResolvedValue([]);
  vi.spyOn(workflowApi, "getRun").mockResolvedValue(run());
  vi.spyOn(itemChatApi, "getChat").mockImplementation(async () => ({
    chatId: "conversation:c1",
    title: "",
    runId: "workflow-run:r1",
    messages: persisted,
  }));
});

const render = (
  onOpenFile = vi.fn(),
  exists: (p: string) => Promise<boolean> = async () => true,
  client?: QueryClient,
) => {
  renderWithQuery(
    <ExecutionView
      slug="hub"
      itemId="it"
      runId="workflow-run:r1"
      profile="default"
      fileService={{ exists } as unknown as FileService}
      onOpenFile={onOpenFile}
    />,
    client,
  );
  return onOpenFile;
};

const row = async (phase: string) =>
  (await screen.findAllByTestId("execution-step")).find((r) => r.dataset.phase === phase)!;
const toggle = (r: HTMLElement) => within(r).getByRole("button", { name: /^(Read|Sort|Write)/ });

describe("ExecutionView", () => {
  it("is named by its workflow and start, and says how far it got", async () => {
    render();
    expect(await screen.findByRole("heading", { name: "Classify wafers · 10/12 14:03" })).toBeInTheDocument();
    expect(screen.getByTestId("execution-status")).toHaveTextContent("Running");
    expect(screen.getByText("Step 2 of 3")).toBeInTheDocument();
  });

  it("has one row per step, the running one open and a finished one closed", async () => {
    render();
    const rows = await screen.findAllByTestId("execution-step");
    expect(rows.map((r) => r.dataset.phase)).toEqual(["read", "sort", "write"]);

    const sort = await row("sort");
    expect(toggle(sort)).toHaveAttribute("aria-expanded", "true");
    expect(await within(sort).findByText("Sort them by defect")).toBeInTheDocument();

    const read = await row("read");
    expect(toggle(read)).toHaveAttribute("aria-expanded", "false");
    expect(within(read).queryByText("Read 12 wafers")).toBeNull();
    fireEvent.click(toggle(read));
    expect(await within(read).findByText("Read 12 wafers")).toBeInTheDocument();
  });

  it("never shows another execution's messages from the same conversation", async () => {
    render();
    await row("write");
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    await within(await row("read")).findByText("Read 12 wafers");
    expect(screen.queryByText("From an older execution")).toBeNull();
  });

  it("expands all, and collapsing all leaves the step being worked on open", async () => {
    render();
    await row("write");
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    for (const p of ["read", "sort", "write"])
      expect(toggle(await row(p))).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(toggle(await row("read"))).toHaveAttribute("aria-expanded", "false");
    expect(toggle(await row("sort"))).toHaveAttribute("aria-expanded", "true");
  });

  it("shows the running step's reply as it streams, before it is saved", async () => {
    liveTail = [
      { kind: "message", message: { role: "assistant", content: "Sorting wafer 3 of 12" } },
    ];
    render();
    expect(await within(await row("sort")).findByText(/Sorting wafer 3 of 12/)).toBeInTheDocument();
  });

  it("stops a live execution, and offers no Stop once it has ended", async () => {
    const cancel = vi.spyOn(workflowApi, "cancelRun").mockResolvedValue();
    render();
    fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(cancel).toHaveBeenCalledWith("hub", "it", "workflow-run:r1"));

    cleanup();
    vi.mocked(workflowApi.getRun).mockResolvedValue(run({ status: "done", ended: STARTED + 1 }));
    render();
    await waitFor(() => expect(screen.getByTestId("execution-status")).toHaveTextContent("Done"));
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });

  it("asks for a gate's decision in the step it waits at", async () => {
    vi.mocked(workflowApi.getRun).mockResolvedValue(
      run({
        status: "awaiting_human",
        pending_decision: { phase: "sort", title: "Check the sort", summary: "", allow: ["approve", "reject"], decided_by: "" },
      }),
    );
    const decide = vi.spyOn(workflowApi, "decide").mockResolvedValue();
    render();
    const sort = await row("sort");
    expect(toggle(sort)).toHaveAttribute("aria-expanded", "true");
    const card = await within(sort).findByTestId("wf-decision-card");
    fireEvent.click(within(card).getByRole("button", { name: /approve/i }));
    await waitFor(() => expect(decide).toHaveBeenCalledWith("hub", "it", "workflow-run:r1", { choice: "approve" }));
  });

  describe("a failed step", () => {
    const failed = () =>
      run({
        status: "error",
        ended: STARTED + 9,
        result: { error: "RuntimeError: sort broke" },
        phases: [
          { phase: "read", status: "passed", done: 1, total: 1, failed: 0 },
          { phase: "sort", status: "failed", done: 0, total: 1, failed: 1 },
        ],
        steps: [
          { phase: "read", name: "read", key: "", status: "passed", attempts: 1, reason: "", started: 1, ended: 2 },
          { phase: "sort", name: "sort", key: "", status: "failed", attempts: 2, reason: "sort broke", started: 3, ended: 4 },
        ],
      });

    it("is open, says what went wrong and how many tries it took", async () => {
      vi.mocked(workflowApi.getRun).mockResolvedValue(failed());
      render();
      const sort = await row("sort");
      expect(toggle(sort)).toHaveAttribute("aria-expanded", "true");
      expect(within(sort).getByText(/sort broke/)).toBeInTheDocument();
      expect(within(sort).getByText("2 attempts")).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
      expect(toggle(await row("sort"))).toHaveAttribute("aria-expanded", "true");
    });

    it("continues from here, or re-runs the step", async () => {
      vi.mocked(workflowApi.getRun).mockResolvedValue(failed());
      const retry = vi.spyOn(workflowApi, "retry").mockResolvedValue();
      render();
      const sort = await row("sort");
      fireEvent.click(within(sort).getByRole("button", { name: "Continue from here" }));
      await waitFor(() => expect(retry).toHaveBeenCalledWith("hub", "it", "workflow-run:r1", ""));
      fireEvent.click(within(sort).getByRole("button", { name: "Re-run this step" }));
      await waitFor(() => expect(retry).toHaveBeenCalledWith("hub", "it", "workflow-run:r1", "sort"));
    });

    it("says why continuing was refused", async () => {
      vi.mocked(workflowApi.getRun).mockResolvedValue(failed());
      vi.spyOn(workflowApi, "retry").mockRejectedValue(new Error("another execution is running"));
      render();
      fireEvent.click(within(await row("sort")).getByRole("button", { name: "Continue from here" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("another execution is running");
    });
  });

  it("opens a step's result and transcript files", async () => {
    const onOpenFile = render();
    const read = await row("read");
    fireEvent.click(toggle(read));
    fireEvent.click(await within(read).findByRole("button", { name: "Result" }));
    expect(onOpenFile).toHaveBeenCalledWith("/.workflow/classify/step_read/main.json");
    fireEvent.click(await within(read).findByRole("button", { name: "Transcript" }));
    expect(onOpenFile).toHaveBeenCalledWith("/.workflow/classify/step_read/main.transcript.md");
  });

  it("changes the execution from its ⋯ menu, then asks before applying the plan", async () => {
    const steer = vi.spyOn(workflowApi, "steer").mockResolvedValue();
    render();
    fireEvent.click(await screen.findByRole("button", { name: "More actions" }));
    fireEvent.click(await screen.findByRole("button", { name: "Change this execution…" }));
    fireEvent.change(screen.getByRole("textbox", { name: "What should change?" }), {
      target: { value: "use the new collection" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Propose the change" }));
    await waitFor(() => expect(steer).toHaveBeenCalledWith("hub", "it", "workflow-run:r1", "use the new collection"));

    cleanup();
    vi.mocked(workflowApi.getRun).mockResolvedValue(
      run({
        status: "awaiting_human",
        pending_steer: { instruction: "use the new collection", rationale: "", input_edits: [], invalidate: ["sort"], decided_by: "" },
      }),
    );
    const confirm = vi.spyOn(workflowApi, "confirmSteer").mockResolvedValue();
    render();
    fireEvent.click(await screen.findByTestId("wf-steer-approve"));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith("hub", "it", "workflow-run:r1", true));
  });
});

// D8 / D9: from an execution, a chat about it — a new one, with the words in
// its box: the results it should read, or the record of the step that broke.
describe("asking the AI about an execution", () => {
  const asked = async () => {
    const { subscribeChatRequest } = await import("../lib/chatRequestBus");
    const drafts: string[] = [];
    const stop = subscribeChatRequest("it", (d) => drafts.push(d));
    return { drafts, stop };
  };

  it("opens a chat with an ended execution's results — only the ones it has", async () => {
    vi.mocked(workflowApi.getRun).mockResolvedValue(
      run({
        status: "error",
        ended: STARTED + 1,
        steps: [
          { phase: "read", name: "read", key: "", status: "passed", attempts: 1, reason: "", started: 1, ended: 2 },
          { phase: "sort", name: "sort", key: "", status: "failed", attempts: 1, reason: "x", started: 3, ended: 4 },
        ],
      }),
    );
    const { drafts, stop } = await asked();
    render();
    fireEvent.click(await screen.findByRole("button", { name: "Open a chat with these results" }));
    expect(drafts).toHaveLength(1);
    expect(drafts[0]).toContain("Classify wafers · 10/12 14:03");
    expect(drafts[0]).toContain("workflow-run:r1");
    expect(drafts[0]).toContain("/.workflow/classify/step_read/main.json");
    // the failed step wrote no result
    expect(drafts[0]).not.toContain("/.workflow/classify/step_sort/main.json");
    expect(drafts[0]).not.toContain("transcript"); // results, not how they were made (D8)
    stop();
  });

  it("offers no results chat while it is still going", async () => {
    render();
    await row("sort");
    expect(screen.queryByRole("button", { name: "Open a chat with these results" })).toBeNull();
  });

  it("asks why a step failed, pointing at its record", async () => {
    vi.mocked(workflowApi.getRun).mockResolvedValue(
      run({
        status: "error",
        ended: STARTED + 9,
        phases: [
          { phase: "read", status: "passed", done: 1, total: 1, failed: 0 },
          { phase: "sort", status: "failed", done: 0, total: 1, failed: 1 },
        ],
        steps: [
          { phase: "read", name: "read", key: "", status: "passed", attempts: 1, reason: "", started: 1, ended: 2 },
          { phase: "sort", name: "sort", key: "", status: "failed", attempts: 2, reason: "sort broke", started: 3, ended: 4 },
        ],
      }),
    );
    const { drafts, stop } = await asked();
    render();
    fireEvent.click(await within(await row("sort")).findByRole("button", { name: "Ask AI why it failed" }));
    await waitFor(() => expect(drafts).toHaveLength(1));
    expect(drafts[0]).toContain("Sort");
    expect(drafts[0]).toContain("sort broke");
    expect(drafts[0]).toContain("/.workflow/classify/step_sort/main.transcript.md");
    expect(drafts[0]).toContain("workflow-run:r1");
    stop();
  });
});

// ── review round 1 ──

const failedRun = (over: Partial<WorkflowRunDTO> = {}) =>
  run({
    status: "error",
    ended: STARTED + 9,
    phases: [
      { phase: "read", status: "passed", done: 1, total: 1, failed: 0 },
      { phase: "sort", status: "failed", done: 0, total: 1, failed: 1 },
    ],
    steps: [
      { phase: "read", name: "read", key: "", status: "passed", attempts: 1, reason: "", started: 1, ended: 2 },
      { phase: "sort", name: "sort", key: "", status: "failed", attempts: 1, reason: "sort broke", started: 3, ended: 4 },
    ],
    latest: true,
    inputs_path: "/uploads/input.json",
    ...over,
  } as Partial<WorkflowRunDTO>);

describe("review round 1", () => {
  it("never collapses the step being worked on, even from its own header (D7)", async () => {
    render();
    const sort = await row("sort");
    expect(toggle(sort)).toHaveAttribute("aria-disabled", "true"); // WAI-ARIA APG
    fireEvent.click(toggle(sort));
    expect(toggle(await row("sort"))).toHaveAttribute("aria-expanded", "true");
  });

  it("offers no Re-run when the step that broke is not a named step on the board", async () => {
    // a loop element failed: it has left the board; the phase's named step passed
    vi.mocked(workflowApi.getRun).mockResolvedValue(
      failedRun({
        steps: [
          { phase: "read", name: "read", key: "", status: "passed", attempts: 1, reason: "", started: 1, ended: 2 },
          { phase: "sort", name: "prepare", key: "", status: "passed", attempts: 1, reason: "", started: 3, ended: 4 },
        ],
      }),
    );
    render();
    const sort = await row("sort");
    expect(await within(sort).findByRole("button", { name: "Continue from here" })).toBeInTheDocument();
    expect(within(sort).queryByRole("button", { name: "Re-run this step" })).toBeNull();
  });

  it("links the inputs the failed step was given (D9)", async () => {
    vi.mocked(workflowApi.getRun).mockResolvedValue(failedRun());
    const onOpenFile = render();
    fireEvent.click(await within(await row("sort")).findByRole("button", { name: "Inputs" }));
    expect(onOpenFile).toHaveBeenCalledWith("/uploads/input.json");
  });

  it("shows a closed step's result in its row (D6)", async () => {
    const onOpenFile = render();
    const read = await row("read");
    expect(toggle(read)).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(within(read).getByRole("button", { name: "Result" }));
    expect(onOpenFile).toHaveBeenCalledWith("/.workflow/classify/step_read/main.json");
  });

  it("an older execution says a newer one has run, and offers nothing that would touch its files", async () => {
    const { subscribeChatRequest } = await import("../lib/chatRequestBus");
    const stop = subscribeChatRequest("it", () => {});
    vi.mocked(workflowApi.getRun).mockResolvedValue(failedRun({ latest: false, step_files: {} }));
    render();
    expect(await screen.findByText(/A newer execution of this workflow has run since/)).toBeInTheDocument();
    const sort = await row("sort");
    expect(within(sort).queryByRole("button", { name: "Continue from here" })).toBeNull();
    expect(within(sort).queryByRole("button", { name: "Re-run this step" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Open a chat with these results" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Result" })).toBeNull();
    // its own record still reads, and the AI can still be asked
    expect(within(sort).getByRole("button", { name: "Ask AI why it failed" })).toBeInTheDocument();
    stop();
  });

  it("offers no chat about the execution where no chat is there to open", async () => {
    vi.mocked(workflowApi.getRun).mockResolvedValue(failedRun());
    render();
    await within(await row("sort")).findByRole("button", { name: "Continue from here" });
    expect(screen.queryByRole("button", { name: "Ask AI why it failed" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Open a chat with these results" })).toBeNull();
  });

  it("says why a proposed change could not be made", async () => {
    vi.mocked(workflowApi.getRun).mockResolvedValue(
      run({ status: "cancelled", ended: STARTED + 2, result: { steer_error: "the model gave no plan" } }),
    );
    render();
    expect(await screen.findByText(/The change could not be proposed: the model gave no plan/)).toBeInTheDocument();
  });

  it("puts a run's messages that belong to no step under Not in a step", async () => {
    persisted.push(msg({ content: "Said outside any step", run_id: "workflow-run:r1" }));
    render();
    const section = await screen.findByRole("region", { name: "Not in a step" });
    expect(within(section).getByText("Said outside any step")).toBeInTheDocument();
  });

  it("asks again whether the transcript exists once the step has moved on", async () => {
    // Review R1: the answer cached while the step ran (no transcript yet) was
    // reused after it failed, so the link and the AI's pointer went missing.
    let written = false;
    vi.mocked(workflowApi.getRun)
      .mockResolvedValueOnce(run())
      .mockResolvedValue(failedRun());
    // the app's own client: its staleTime is what kept the stale answer
    render(vi.fn(), async () => written, makeQueryClient());
    await row("sort");
    written = true; // the turn wrote it, then the step failed
    expect(
      await within(await row("sort")).findByRole("button", { name: "Transcript" }, { timeout: 4000 }),
    ).toBeInTheDocument();
  });
});

describe("a row closed by hand", () => {
  it("opens again when its step fails — a failed step is never collapsed (D7)", async () => {
    vi.mocked(workflowApi.getRun)
      .mockResolvedValueOnce(run())
      .mockResolvedValue(
        failedRun({
          phases: [
            { phase: "read", status: "passed", done: 1, total: 1, failed: 0 },
            { phase: "sort", status: "passed", done: 1, total: 1, failed: 0 },
            { phase: "write", status: "failed", done: 0, total: 1, failed: 1 },
          ],
          current_phase: "write",
        }),
      );
    render();
    fireEvent.click(toggle(await row("write"))); // open it while pending…
    fireEvent.click(toggle(await row("write"))); // …and close it again
    expect(toggle(await row("write"))).toHaveAttribute("aria-expanded", "false");
    // then its step fails: the row opens, whatever the hand did before
    await waitFor(
      () =>
        expect(toggle(screen.getAllByTestId("execution-step")[2])).toHaveAttribute(
          "aria-expanded",
          "true",
        ),
      { timeout: 4000 },
    );
  });
});
