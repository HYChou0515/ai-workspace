// @vitest-environment happy-dom
/**
 * The Workflows panel's schedules section — where a person SEES what the agent
 * put on a clock. Until this, `.workflows/schedules.json` lived in a folder the
 * IDE tree hides: the agent said "set up" and the person could only believe it.
 *
 * The rows are the schedules overview's, narrowed to this item — its own file
 * and each page's (docs/plan-schedule-run-as.md G10) — drawn with the overview's
 * own parts and words (G9), stacked; their acts — Run now, Edit time, Remove,
 * who it runs as — go through the row routes the overview shares.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileService } from "../api/fileService";
import { workflowTemplatesApi } from "../api/workflowTemplates";
import type { OverviewRow, ScheduleOverview } from "../api/schedules";
import { translate } from "../lib/i18n";
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
      itemOverview: (...a: unknown[]) => schedulesMock(...a),
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

const word = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);

const OWN = "/.workflows/schedules.json";

const NIGHTLY: OverviewRow = {
  slug: "playground",
  item_id: "inv1",
  item_title: "Line 3",
  item_owner: "bob",
  path: OWN,
  index: 0,
  raw: { every: "daily", at: "09:00", tz: "Asia/Taipei", run: "nightly" },
  problems: [],
  run: "nightly",
  describe: "daily at 09:00 Asia/Taipei",
  next_at: "2026-09-16 09:00",
  due_now: false,
  tz: "Asia/Taipei",
  known: true,
  run_problem: "",
  runnable: true,
  trigger_id: "wui:it:nightly",
  next_ms: Date.UTC(2026, 8, 16, 1, 0),
  run_title: "Nightly report",
  last_run: null,
  can_edit: true,
  can_run: true,
  can_read: true,
  page_path: "",
  page_title: "",
  runs_as: "",
  runs_as_reason: "",
  runs_as_by: "",
  runs_as_mine: false,
};

// The viewer reads in Taipei, on 2026-09-15 at 10:00 — pinned, because the
// machine a test runs on has a zone of its own.
const VIEWER = "Asia/Taipei";
const NOW = Date.UTC(2026, 8, 15, 2, 0);

function schedules(over: Partial<ScheduleOverview> = {}): ScheduleOverview {
  return { enabled: true, rows: [NIGHTLY], files: [], can_assign: false, unindexed: [], ...over };
}

const capProblem = (problems: string[]) => [
  { slug: "playground", item_id: "inv1", item_title: "Line 3", path: OWN, problems },
];

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
  // Inside the item page's router in the app: "See it" and "Last run" are links.
  return renderWithQuery(
    <MemoryRouter>
      <ViewerClockPin viewer={VIEWER} now={NOW}>
        <DialogProvider>
          <WorkflowsModal slug="playground" itemId="inv1" fileService={svc} onClose={() => {}} />
        </DialogProvider>
      </ViewerClockPin>
    </MemoryRouter>,
  );
}

/** The row's Remove — its name says which schedule (the overview's label). */
const removeButton = async () =>
  within(await screen.findByTestId("schedule-row-0")).getByRole("button", {
    name: new RegExp(`^${word("schedules.remove")} `),
  });

describe("WorkflowsModal — schedules", () => {
  it("lists each schedule with its recurrence, the workflow it runs and when it runs next", async () => {
    listMock.mockResolvedValue([{ id: "nightly", title: "Nightly report", phases: [{ id: "a" }] }]);
    schedulesMock.mockResolvedValue(schedules());
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    expect(row).toHaveTextContent("Nightly report"); // the workflow's title, not its id
    expect(row).toHaveTextContent("每天 09:00"); // zh-TW is the test locale
    // On the viewer's clock, said the way a person says it — no zone id —
    // under the overview's own column name (G9).
    expect(row).toHaveTextContent(`${word("scheduleOverview.col.next")}明天 09:00`);
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
      schedules({ rows: [{ ...NIGHTLY, run: "vanished", run_title: "", known: false, runnable: false, next_at: "" }] }),
    );
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    expect(row).toHaveTextContent("vanished");
    expect(row).toHaveTextContent(word("schedules.unknownWorkflow"));
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
      schedules({ rows: [{ ...NIGHTLY, next_at: "", due_now: true }] }),
    );
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    expect(row).toHaveTextContent(word("scheduleOverview.nextSweep"));
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
    const held = { ...NIGHTLY, runnable: false, next_at: "", next_ms: null, due_now: false };
    schedulesMock.mockResolvedValue(
      schedules({ rows: [held, { ...held, index: 1 }, { ...held, index: 2 }], files: capProblem([cap]) }),
    );
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    expect(row).toHaveTextContent("Nightly report");
    expect(row).not.toHaveTextContent("明天");
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
      schedules({ unindexed: [OWN], rows: [{ ...NIGHTLY, runnable: false, next_at: "", next_ms: null }] }),
    );
    render(fakeService().svc);

    expect(await screen.findByTestId("schedules-unindexed")).toBeInTheDocument();
    expect(screen.getByTestId("schedule-row-0")).not.toHaveTextContent("明天");
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
      schedules({ enabled: false, unindexed: [OWN], rows: [{ ...NIGHTLY, runnable: false }] }),
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
            next_at: "",
            next_ms: null,
            run_problem: "steps[0]: `cache` is required — …",
          },
        ],
      }),
    );
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    expect(row).toHaveTextContent("`cache` is required");
    expect(row).not.toHaveTextContent("明天");
    expect(row).not.toHaveTextContent(word("schedules.unknownWorkflow"));
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

    const button = await removeButton();
    // The destructive act looks like one — the overview's own Remove (G9).
    expect(button).toHaveAttribute("data-variant", "danger");
    fireEvent.click(button);
    fireEvent.click(await screen.findByRole("button", { name: "移除" }));

    await waitFor(() =>
      expect(removeMock).toHaveBeenCalledWith("playground", "inv1", {
        path: OWN,
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

    fireEvent.click(await removeButton());
    fireEvent.click(await screen.findByRole("button", { name: "移除" }));

    await waitFor(() =>
      expect(removeMock).toHaveBeenCalledWith("playground", "inv1", {
        path: OWN,
        trigger_id: "",
        raw: 5,
        index: 0,
      }),
    );
    // Nothing to move or run on a row that never fires.
    expect(screen.queryByRole("button", { name: word("schedules.editTime") })).toBeNull();
    expect(screen.queryByRole("button", { name: word("schedules.runNow") })).toBeNull();
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

    fireEvent.click(await removeButton());
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

    fireEvent.click(await removeButton());
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "移除排程" })).toBeNull());
    expect(removeMock).not.toHaveBeenCalled();
  });

  it("a row changed meanwhile is not rewritten — the server's sentence is shown", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules());
    removeMock.mockRejectedValue(new ScheduleActionError(409, "This schedule has changed"));
    render(fakeService().svc);

    fireEvent.click(await removeButton());
    fireEvent.click(await screen.findByRole("button", { name: "移除" }));

    expect(await within(screen.getByTestId("schedule-row-0")).findByRole("alert")).toHaveTextContent(
      "This schedule has changed",
    );
    // …and the list is read again, so it shows what the file holds NOW.
    await waitFor(() => expect(schedulesMock.mock.calls.length).toBeGreaterThan(1));
  });

  it("offers a reader no way to change or run a schedule", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules({ rows: [{ ...NIGHTLY, can_edit: false, can_run: false }] }));
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    // Not Remove, not Edit time, not Run now — and not "Run as me" either.
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("runs a schedule now and says so", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules());
    runNowMock.mockResolvedValue("run-1");
    render(fakeService().svc);

    const { QueryClient } = await import("@tanstack/react-query");
    const { qk } = await import("../api/queryKeys");
    const spy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    fireEvent.click(await screen.findByRole("button", { name: word("schedules.runNow") }));

    expect(await within(screen.getByTestId("schedule-row-0")).findByRole("status")).toHaveTextContent(
      word("schedules.started"),
    );
    // The run is an execution: this item's Workflows sidebar list refreshes now
    // (plan-workflow-out-of-chat D7; the schedule's conversation is not a chat).
    await waitFor(() =>
      expect(spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey))).toContain(
        JSON.stringify(qk.workflowRuns("playground", "inv1")),
      ),
    );
    expect(runNowMock).toHaveBeenCalledWith("playground", "inv1", {
      path: OWN,
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

    fireEvent.click(await screen.findByRole("button", { name: word("schedules.editTime") }));
    const modal = await screen.findByTestId("schedule-time-modal");
    fireEvent.change(within(modal).getByLabelText("時"), { target: { value: "07" } });
    fireEvent.change(within(modal).getByLabelText("分"), { target: { value: "15" } });
    fireEvent.click(within(modal).getByTestId("schedule-time-save"));

    await waitFor(() => expect(screen.queryByTestId("schedule-time-modal")).toBeNull());
    expect(editTimeMock).toHaveBeenCalledWith(
      "playground",
      "inv1",
      { path: OWN, trigger_id: "wui:it:nightly", raw: undefined, index: undefined },
      expect.objectContaining({ every: "daily", at: "07:15", tz: "Asia/Taipei" }),
    );
  });

  it("lists a page's schedules too, saying which page — the overview narrowed to this item (G10)", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(
      schedules({
        rows: [NIGHTLY, { ...NIGHTLY, path: "/report/schedules.json", page_title: "Weekly report", trigger_id: "k2" }],
      }),
    );
    render(fakeService().svc);

    expect(await screen.findByTestId("schedule-row-0")).toHaveTextContent(word("scheduleOverview.itemOwn"));
    expect(screen.getByTestId("schedule-row-1")).toHaveTextContent(
      word("scheduleOverview.page", { page: "Weekly report" }),
    );
    expect(schedulesMock).toHaveBeenCalledWith("playground", "inv1");
  });

  it("says who each schedule runs as and why, with the overview's words (G8/G9)", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules({ rows: [{ ...NIGHTLY, runs_as: "bob", runs_as_reason: "author" }] }));
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    expect(row).toHaveTextContent(word("scheduleOverview.col.runsAs"));
    expect(within(row).getByTestId("runs-as")).toHaveTextContent(
      word("sched.runsAsWhy", { main: word("sched.runsAs", { who: "bob" }), why: word("sched.why.author") }),
    );
    expect(within(row).getByRole("button", { name: word("sched.replaceMe") })).toBeInTheDocument();
  });

  it("a row's own state stays with that row when the one above it goes", async () => {
    // Review round 1 (defect 8): keyed by position, "started" moved to the row
    // that slid into the removed row's place.
    listMock.mockResolvedValue([]);
    const other = { ...NIGHTLY, index: 1, trigger_id: "wui:it:other", run_title: "Other" };
    schedulesMock.mockResolvedValueOnce(schedules({ rows: [NIGHTLY, other] }));
    schedulesMock.mockResolvedValue(schedules({ rows: [{ ...other, index: 0 }] }));
    runNowMock.mockResolvedValue("run-1");
    render(fakeService().svc);

    const first = await screen.findByTestId("schedule-row-0");
    fireEvent.click(within(first).getByRole("button", { name: word("schedules.runNow") }));

    await waitFor(() => expect(screen.getByTestId("schedule-row-0")).toHaveTextContent("Other"));
    expect(screen.getByTestId("schedule-row-0")).not.toHaveTextContent(word("schedules.started"));
  });
});
