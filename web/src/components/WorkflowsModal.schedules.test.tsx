// @vitest-environment happy-dom
/**
 * The Workflows panel's schedules section — where a person SEES what the agent
 * put on a clock. Until this, `.workflows/schedules.json` lived in a folder the
 * IDE tree hides: the agent said "set up" and the person could only believe it.
 *
 * The rows come from the backend already interpreted (same parser and next-run
 * rule as the sweep); this file renders them and their acts — Run now, Edit
 * time, Remove — which go through the row routes the schedules overview
 * shares (docs/plan-schedule-overview.md §3).
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileService } from "../api/fileService";
import { workflowTemplatesApi } from "../api/workflowTemplates";
import type { ItemSchedules } from "../api/schedules";
import { renderWithQuery } from "../test/queryWrapper";
import { ViewerClockPin } from "../lib/viewerClock";
import { DialogProvider } from "./Dialog";
import { ScheduleActionError } from "../api/schedules";
import { WorkflowsModal } from "./WorkflowsModal";

const listMock = vi.fn();
vi.mock("../api/workspaceWorkflows", async (orig) => {
  const actual = await orig<typeof import("../api/workspaceWorkflows")>();
  return { ...actual, workspaceWorkflowsApi: { list: (...a: unknown[]) => listMock(...a) } };
});
const schedulesMock = vi.fn();
const removeMock = vi.fn();
const runNowMock = vi.fn();
const editTimeMock = vi.fn();
vi.mock("../api/schedules", async (orig) => {
  const actual = await orig<typeof import("../api/schedules")>();
  return {
    ...actual,
    schedulesApi: {
      list: (...a: unknown[]) => schedulesMock(...a),
      remove: (...a: unknown[]) => removeMock(...a),
      runNow: (...a: unknown[]) => runNowMock(...a),
      editTime: (...a: unknown[]) => editTimeMock(...a),
    },
  };
});
vi.mock("../api/workflows", () => ({ workflowApi: { startRun: vi.fn() } }));
vi.mock("../api/workflowTemplates", () => ({
  workflowTemplatesApi: { list: vi.fn(async () => []), copy: vi.fn() },
  TemplateConflictError: class extends Error {},
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  listMock.mockReset();
  schedulesMock.mockReset();
  removeMock.mockReset();
  runNowMock.mockReset();
  editTimeMock.mockReset();
});

const NIGHTLY = {
  index: 0,
  raw: { every: "daily", at: "09:00", tz: "Asia/Taipei", run: "nightly" },
  problems: [],
  run: "nightly",
  describe: "daily at 09:00 Asia/Taipei",
  next_run: "2026-09-16 09:00 Asia/Taipei",
  next_at: "2026-09-16 09:00",
  due_now: false,
  tz: "Asia/Taipei",
  known: true,
  run_problem: "",
  runnable: true,
  payload: {},
  trigger_id: "wui:it:nightly",
  next_ms: Date.UTC(2026, 8, 16, 1, 0),
  run_title: "Nightly report",
};

// The viewer reads in Taipei, on 2026-09-15 at 10:00 — pinned, because the
// machine a test runs on has a zone of its own.
const VIEWER = "Asia/Taipei";
const NOW = Date.UTC(2026, 8, 15, 2, 0);

function schedules(over: Partial<ItemSchedules> = {}): ItemSchedules {
  return {
    enabled: true,
    indexed: true,
    path: ".workflows/schedules.json",
    rows: [NIGHTLY],
    problems: [],
    can_edit: true,
    can_run: true,
    can_read: true,
    ...over,
  };
}

function fakeService() {
  const writes: { path: string; body: string }[] = [];
  const writeFile = vi.fn(async (path: string, body: ArrayBuffer | string) => {
    writes.push({ path, body: typeof body === "string" ? body : new TextDecoder().decode(body) });
  });
  const svc = {
    scopeId: "inv1",
    prepareDirDownload: vi.fn(),
    dirDownloadUrl: vi.fn(),
    writeFile,
  } as unknown as FileService;
  return { svc, writes };
}

function render(svc: FileService) {
  return renderWithQuery(
    <ViewerClockPin viewer={VIEWER} now={NOW}>
      <DialogProvider>
        <WorkflowsModal slug="playground" itemId="inv1" fileService={svc} onClose={() => {}} />
      </DialogProvider>
    </ViewerClockPin>,
  );
}

describe("WorkflowsModal — schedules", () => {
  it("lists each schedule with its recurrence, the workflow it runs and when it runs next", async () => {
    listMock.mockResolvedValue([{ id: "nightly", title: "Nightly report", phases: [{ id: "a" }] }]);
    schedulesMock.mockResolvedValue(schedules());
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    expect(row).toHaveTextContent("Nightly report"); // the workflow's title, not its id
    expect(row).toHaveTextContent("每天 09:00"); // zh-TW is the test locale
    // On the viewer's clock, said the way a person says it — no zone id.
    expect(row).toHaveTextContent("下次：明天 09:00");
    expect(row).not.toHaveTextContent("Asia/Taipei");
  });

  it("says nothing when the item has no schedules", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules({ rows: [] }));
    render(fakeService().svc);

    expect(await screen.findByTestId("workflows-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("schedules-section")).not.toBeInTheDocument();
  });

  it("flags a row whose workflow no longer exists — the sweep skips it in silence", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(
      schedules({ rows: [{ ...NIGHTLY, run: "vanished", known: false, runnable: false, next_run: "", next_at: "" }] }),
    );
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    expect(row).toHaveTextContent("vanished");
    expect(screen.getByTestId("schedule-unknown-0")).toBeInTheDocument();
  });

  it("shows a refused row with its problem, so the line can be fixed or removed", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(
      schedules({
        rows: [
          {
            ...NIGHTLY,
            raw: { every: "day", run: "nightly" },
            run: "",
            problems: ["schedules[0]: `every` must be one of minutes, hourly, daily, weekly, monthly."],
          },
        ],
      }),
    );
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    expect(row).toHaveTextContent("`every` must be one of");
  });

  it("says a due row runs on the next sweep rather than inventing a time", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(
      schedules({ rows: [{ ...NIGHTLY, next_at: "", due_now: true, next_run: "on the next sweep (this period is already due and has not run yet)" }] }),
    );
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    expect(row).toHaveTextContent("下一輪");
    expect(row).not.toHaveTextContent("2026-09-16");
  });

  it("warns when this deployment does not run schedules at all", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules({ enabled: false }));
    render(fakeService().svc);

    expect(await screen.findByTestId("schedules-disabled")).toBeInTheDocument();
  });

  it("a row without `every` reads as daily, the way the sweep reads it", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(
      schedules({ rows: [{ ...NIGHTLY, raw: { at: "09:00", run: "nightly" } }] }),
    );
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    // No `tz` is UTC, the zone the sweep fires it by: 17:00 in Taipei.
    expect(row).toHaveTextContent("每天 17:00");
    expect(row).not.toHaveTextContent("?");
  });

  it("over the cap: the file says why, each row keeps its name and gets no next time", async () => {
    // The backend's verdict is `runnable`; the panel never invents a "next"
    // for a row the sweep will skip. The cap is the FILE's problem, said once —
    // a well-formed row is not "written wrong" and keeps its workflow's title.
    listMock.mockResolvedValue([{ id: "nightly", title: "Nightly report", phases: [{ id: "a" }] }]);
    const cap = "declares 3 schedules, over the limit of 2 — none will run until it is reduced";
    const held = { ...NIGHTLY, runnable: false, next_run: "", next_at: "", due_now: false };
    schedulesMock.mockResolvedValue(
      schedules({ rows: [held, { ...held, index: 1 }, { ...held, index: 2 }], problems: [cap] }),
    );
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    expect(row).toHaveTextContent("Nightly report");
    expect(row).not.toHaveTextContent("下次");
    expect(row).not.toHaveTextContent("寫得不對");
    // Said once, under a label that says "problems", not "could not be read".
    expect(screen.getByTestId("schedules-file-problems")).toHaveTextContent("over the limit of 2");
    expect(screen.getByTestId("schedules-file-problems")).not.toHaveTextContent("讀不出來");
    expect(screen.getAllByText(/over the limit of 2/)).toHaveLength(1);
  });

  it("a period left unset in any of the ways the parser accepts reads as daily", async () => {
    // The parser's rule is `row.get("every") or "daily"`: absent, null, "", 0
    // and false all run daily at 00:00 — the panel says the same, never "0".
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(
      schedules({
        rows: [
          { ...NIGHTLY, index: 0, raw: { every: 0, run: "nightly" } },
          { ...NIGHTLY, index: 1, raw: { every: false, at: "", run: "nightly" } },
        ],
      }),
    );
    render(fakeService().svc);

    expect(await screen.findByTestId("schedule-row-0")).toHaveTextContent(/^每天 08:00 →/);
    expect(screen.getByTestId("schedule-row-1")).toHaveTextContent(/^每天 08:00 →/);
    expect(screen.getByTestId("schedule-row-1")).not.toHaveTextContent("false");
  });

  it("importing a file refreshes the schedules too — a schedules.json is a legitimate import", async () => {
    const { QueryClient } = await import("@tanstack/react-query");
    const { qk } = await import("../api/queryKeys");
    const spy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules());
    const { svc } = fakeService();
    render(svc);
    await screen.findByTestId("schedule-row-0");
    const before = schedulesMock.mock.calls.length;

    const input = screen.getByTestId("workflows-import-input") as HTMLInputElement;
    const file = new File(['{"schedules": []}'], "schedules.json", { type: "application/json" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(schedulesMock.mock.calls.length).toBeGreaterThan(before));
    // …and the schedules overview, which lists the same rows (decision 9).
    await waitFor(() =>
      expect(spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey))).toContain(
        JSON.stringify(qk.schedulesOverview),
      ),
    );
  });

  it("says when the sweep has not been told about the file yet", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(
      schedules({ indexed: false, rows: [{ ...NIGHTLY, runnable: false, next_run: "", next_at: "" }] }),
    );
    render(fakeService().svc);

    expect(await screen.findByTestId("schedules-unindexed")).toBeInTheDocument();
    expect(screen.getByTestId("schedule-row-0")).not.toHaveTextContent("下次");
  });

  it("a row whose `every` is null reads as daily, as the parser reads it", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(
      schedules({ rows: [{ ...NIGHTLY, raw: { every: null, at: "09:00", run: "nightly" } }] }),
    );
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    expect(row).toHaveTextContent("每天 17:00");
    expect(row).not.toHaveTextContent("null");
  });

  it("a deployment with the sweep off gets ONE notice — not also 'starts after the next turn'", async () => {
    // The two file-level notices contradict each other: "will not run on its
    // own" and "starts running on its own after the next conversation turn".
    // The index is kept whether the sweep runs or not, so a file the index has
    // not seen on a sweep-off deployment is a file that will not run, full stop.
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(
      schedules({ enabled: false, indexed: false, rows: [{ ...NIGHTLY, runnable: false }] }),
    );
    render(fakeService().svc);

    expect(await screen.findByTestId("schedules-disabled")).toBeInTheDocument();
    expect(screen.queryByTestId("schedules-unindexed")).toBeNull();
  });

  it("copying a template refreshes the schedules too — a red row may just have found its workflow", async () => {
    // Same door as import, one button over: a row whose `run` names a
    // workflow the item lacks reads red until the workflow exists, and Copy is
    // one way it comes to exist.
    const { QueryClient } = await import("@tanstack/react-query");
    const { qk } = await import("../api/queryKeys");
    const spy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules());
    vi.mocked(workflowTemplatesApi.list).mockResolvedValue([
      {
        id: "nightly",
        title: "Nightly",
        description: "",
        tag: "",
        hint: "",
        phases: [],
        compatible: true,
        problems: [],
      },
    ]);
    render(fakeService().svc);
    await screen.findByTestId("schedule-row-0");
    const before = schedulesMock.mock.calls.length;

    fireEvent.click(await screen.findByTestId("workflow-template-copy-nightly"));

    await waitFor(() => expect(schedulesMock.mock.calls.length).toBeGreaterThan(before));
    // …and the schedules overview, which lists the same rows (decision 9).
    await waitFor(() =>
      expect(spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey))).toContain(
        JSON.stringify(qk.schedulesOverview),
      ),
    );
  });

  it("an empty object or list for `every` reads as daily — Python's `or`, not JavaScript's", async () => {
    // `{}` and `[]` are falsy to the parser (`row.get("every") or "daily"`) and
    // truthy to `||`; the sweep runs both rows daily, so the panel must not
    // draw "[object Object]" or a blank period beside a real next time.
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(
      schedules({
        rows: [
          { ...NIGHTLY, index: 0, raw: { every: {}, run: "nightly" } },
          { ...NIGHTLY, index: 1, raw: { every: [], at: [], run: "nightly" } },
        ],
      }),
    );
    render(fakeService().svc);

    expect(await screen.findByTestId("schedule-row-0")).toHaveTextContent(/^每天 08:00 →/);
    expect(screen.getByTestId("schedule-row-1")).toHaveTextContent(/^每天 08:00 →/);
  });

  it("says when the workflow a row names will not parse — the row is fine, the workflow is not", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(
      schedules({
        rows: [
          {
            ...NIGHTLY,
            runnable: false,
            next_run: "",
            next_at: "",
            run_problem: "steps[0]: `cache` is required — …",
          },
        ],
      }),
    );
    render(fakeService().svc);

    const note = await screen.findByTestId("schedule-broken-0");
    expect(note).toHaveTextContent("`cache` is required");
    expect(screen.getByTestId("schedule-row-0")).not.toHaveTextContent("下次");
    expect(screen.queryByTestId("schedule-unknown-0")).toBeNull();
  });

  // One row's acts go through the row routes (docs/plan-schedule-overview.md
  // §3); what the panel used to guarantee itself — every other row kept as
  // written, a fresh read, list order as identity — is held by the server
  // now and tested there (tests/api/test_schedule_overview.py).
  it("removing a row asks the server to drop exactly that row, by its identity", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules());
    removeMock.mockResolvedValue(undefined);
    render(fakeService().svc);

    const button = await screen.findByTestId("schedule-remove-0");
    // The destructive act looks like one (the overview's Remove does too).
    // (happy-dom drops `var()` from computed style, so read the attribute.)
    expect(button.getAttribute("style")).toContain("color: var(--err)");
    fireEvent.click(button);
    fireEvent.click(await screen.findByRole("button", { name: "移除" }));

    await waitFor(() =>
      expect(removeMock).toHaveBeenCalledWith("playground", "inv1", {
        path: ".workflows/schedules.json",
        trigger_id: "wui:it:nightly",
        raw: undefined,
        index: undefined,
      }),
    );
  });

  it("a refused row has no identity, so it is removed by what it says", async () => {
    listMock.mockResolvedValue([]);
    const notAnObject = { ...NIGHTLY, raw: 5, run: "", trigger_id: "", problems: ["must be an object"] };
    schedulesMock.mockResolvedValue(schedules({ rows: [notAnObject] }));
    removeMock.mockResolvedValue(undefined);
    render(fakeService().svc);

    fireEvent.click(await screen.findByTestId("schedule-remove-0"));
    fireEvent.click(await screen.findByRole("button", { name: "移除" }));

    await waitFor(() =>
      expect(removeMock).toHaveBeenCalledWith("playground", "inv1", {
        path: ".workflows/schedules.json",
        trigger_id: "",
        raw: 5,
        index: 0,
      }),
    );
    // Nothing to move or run on a row that never fires.
    expect(screen.queryByTestId("schedule-edit-0")).toBeNull();
    expect(screen.queryByTestId("schedule-run-0")).toBeNull();
  });

  it("a row removed here also refreshes the schedules overview", async () => {
    // Decision 9: the two entrances list the same rows; each refreshes the other.
    const { QueryClient } = await import("@tanstack/react-query");
    const { qk } = await import("../api/queryKeys");
    const spy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules());
    removeMock.mockResolvedValue(undefined);
    render(fakeService().svc);

    fireEvent.click(await screen.findByTestId("schedule-remove-0"));
    fireEvent.click(await screen.findByRole("button", { name: "移除" }));

    await waitFor(() =>
      expect(spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey))).toContain(
        JSON.stringify(qk.schedulesOverview),
      ),
    );
  });

  it("removing asks once, and a cancel sends nothing", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules());
    render(fakeService().svc);

    fireEvent.click(await screen.findByTestId("schedule-remove-0"));
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "移除排程" })).toBeNull());
    expect(removeMock).not.toHaveBeenCalled();
  });

  it("a row changed meanwhile is not rewritten — the server's sentence is shown", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules());
    removeMock.mockRejectedValue(new ScheduleActionError(409, "This schedule has changed"));
    render(fakeService().svc);

    fireEvent.click(await screen.findByTestId("schedule-remove-0"));
    fireEvent.click(await screen.findByRole("button", { name: "移除" }));

    expect(await screen.findByTestId("schedule-said-0")).toHaveTextContent("This schedule has changed");
  });

  it("offers a reader no way to change or run a schedule", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules({ can_edit: false, can_run: false }));
    render(fakeService().svc);

    await screen.findByTestId("schedule-row-0");
    expect(screen.queryByTestId("schedule-remove-0")).toBeNull();
    expect(screen.queryByTestId("schedule-edit-0")).toBeNull();
    expect(screen.queryByTestId("schedule-run-0")).toBeNull();
  });

  it("runs a schedule now and says so", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules());
    runNowMock.mockResolvedValue("run-1");
    render(fakeService().svc);

    const { QueryClient } = await import("@tanstack/react-query");
    const { qk } = await import("../api/queryKeys");
    const spy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    fireEvent.click(await screen.findByTestId("schedule-run-0"));

    expect(await screen.findByTestId("schedule-said-0")).toHaveTextContent("已開始執行");
    // The run may have made the schedule's own chat: this item's chat list refreshes.
    await waitFor(() =>
      expect(spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey))).toContain(
        JSON.stringify(qk.itemChats("playground", "inv1")),
      ),
    );
    expect(runNowMock).toHaveBeenCalledWith("playground", "inv1", {
      path: ".workflows/schedules.json",
      trigger_id: "wui:it:nightly",
      raw: undefined,
      index: undefined,
    });
  });

  it("moves a schedule to a new time with the editor the overview uses", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules());
    editTimeMock.mockResolvedValue(undefined);
    render(fakeService().svc);

    fireEvent.click(await screen.findByTestId("schedule-edit-0"));
    const modal = await screen.findByTestId("schedule-time-modal");
    fireEvent.change(within(modal).getByLabelText("時"), { target: { value: "07" } });
    fireEvent.change(within(modal).getByLabelText("分"), { target: { value: "15" } });
    fireEvent.click(within(modal).getByTestId("schedule-time-save"));

    await waitFor(() => expect(screen.queryByTestId("schedule-time-modal")).toBeNull());
    expect(editTimeMock).toHaveBeenCalledWith(
      "playground",
      "inv1",
      { path: ".workflows/schedules.json", trigger_id: "wui:it:nightly", raw: undefined, index: undefined },
      expect.objectContaining({ every: "daily", at: "07:15", tz: "Asia/Taipei" }),
    );
  });
});
