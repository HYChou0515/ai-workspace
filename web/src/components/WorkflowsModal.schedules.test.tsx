// @vitest-environment happy-dom
/**
 * The Workflows panel's schedules section — where a person SEES what the agent
 * put on a clock. Until this, `.workflows/schedules.json` lived in a folder the
 * IDE tree hides: the agent said "set up" and the person could only believe it.
 *
 * The rows come from the backend already interpreted (same parser and next-run
 * rule as the sweep); this file renders and removes. Removing rewrites the file
 * minus one row through the ordinary file write, so it lands on the path the
 * platform indexes — no schedule-specific write route.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileService } from "../api/fileService";
import { workflowTemplatesApi } from "../api/workflowTemplates";
import type { ItemSchedules } from "../api/schedules";
import { renderWithQuery } from "../test/queryWrapper";
import { DialogProvider } from "./Dialog";
import { WorkflowsModal } from "./WorkflowsModal";

const listMock = vi.fn();
vi.mock("../api/workspaceWorkflows", async (orig) => {
  const actual = await orig<typeof import("../api/workspaceWorkflows")>();
  return { ...actual, workspaceWorkflowsApi: { list: (...a: unknown[]) => listMock(...a) } };
});
const schedulesMock = vi.fn();
vi.mock("../api/schedules", async (orig) => {
  const actual = await orig<typeof import("../api/schedules")>();
  return { ...actual, schedulesApi: { list: (...a: unknown[]) => schedulesMock(...a) } };
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
  runnable: true,
  payload: {},
};

function schedules(over: Partial<ItemSchedules> = {}): ItemSchedules {
  return {
    enabled: true,
    indexed: true,
    path: ".workflows/schedules.json",
    rows: [NIGHTLY],
    problems: [],
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
    <DialogProvider>
      <WorkflowsModal slug="playground" itemId="inv1" fileService={svc} onClose={() => {}} />
    </DialogProvider>,
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
    expect(row).toHaveTextContent("Asia/Taipei");
    expect(row).toHaveTextContent("2026-09-16 09:00");
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

  it("removing a row rewrites the file without it — and keeps the rows it did not touch, refused ones included", async () => {
    listMock.mockResolvedValue([]);
    const bad = {
      ...NIGHTLY,
      index: 1,
      raw: { every: "day", run: "nightly" },
      run: "",
      problems: ["schedules[1]: `every` must be one of minutes, hourly, daily, weekly, monthly."],
    };
    const hourly = { ...NIGHTLY, index: 2, raw: { every: "hourly", run: "nightly" } };
    schedulesMock.mockResolvedValue(schedules({ rows: [NIGHTLY, bad, hourly] }));
    const { svc, writes } = fakeService();
    render(svc);

    fireEvent.click(await screen.findByTestId("schedule-remove-0"));
    fireEvent.click(await screen.findByRole("button", { name: "移除" }));

    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].path).toBe(".workflows/schedules.json");
    expect(JSON.parse(writes[0].body)).toEqual({
      schedules: [{ every: "day", run: "nightly" }, { every: "hourly", run: "nightly" }],
    });
  });

  it("removing rewrites from the FILE AS IT IS NOW, not from what the panel loaded earlier", async () => {
    // The panel opened with [A]. Meanwhile the agent's save_schedules added B.
    // Removing A from the loaded list would write [] — cancelling B, which
    // nobody asked to cancel. The rewrite must start from a fresh read.
    listMock.mockResolvedValue([]);
    const b = { ...NIGHTLY, index: 1, raw: { every: "hourly", run: "nightly" } };
    schedulesMock.mockResolvedValueOnce(schedules({ rows: [NIGHTLY] }));
    schedulesMock.mockResolvedValue(schedules({ rows: [NIGHTLY, b] }));
    const { svc, writes } = fakeService();
    render(svc);

    fireEvent.click(await screen.findByTestId("schedule-remove-0"));
    fireEvent.click(await screen.findByRole("button", { name: "移除" }));

    await waitFor(() => expect(writes).toHaveLength(1));
    expect(JSON.parse(writes[0].body)).toEqual({
      schedules: [{ every: "hourly", run: "nightly" }],
    });
  });

  it("removing a row that is already gone writes nothing", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValueOnce(schedules({ rows: [NIGHTLY] }));
    schedulesMock.mockResolvedValue(schedules({ rows: [] })); // the agent removed it first
    const { svc, writes } = fakeService();
    render(svc);

    fireEvent.click(await screen.findByTestId("schedule-remove-0"));
    fireEvent.click(await screen.findByRole("button", { name: "移除" }));

    await waitFor(() => expect(screen.queryByTestId("schedule-row-0")).toBeNull());
    expect(writes).toHaveLength(0);
  });

  it("a row without `every` reads as daily, the way the sweep reads it", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(
      schedules({ rows: [{ ...NIGHTLY, raw: { at: "09:00", run: "nightly" } }] }),
    );
    render(fakeService().svc);

    const row = await screen.findByTestId("schedule-row-0");
    expect(row).toHaveTextContent("每天 09:00");
    expect(row).not.toHaveTextContent("?");
  });

  it("a row that is not an object is written back exactly as it was", async () => {
    // `5` is refused ("must be an object") — and stays `5` in the file after a
    // rewrite, not a substitute the author never typed.
    listMock.mockResolvedValue([]);
    const notAnObject = { ...NIGHTLY, index: 0, raw: 5, run: "", problems: ["schedules[0]: each schedule must be an object."] };
    const nightly = { ...NIGHTLY, index: 1 };
    schedulesMock.mockResolvedValue(schedules({ rows: [notAnObject, nightly] }));
    const { svc, writes } = fakeService();
    render(svc);

    expect(await screen.findByTestId("schedule-row-0")).toHaveTextContent("must be an object");
    fireEvent.click(screen.getByTestId("schedule-remove-1"));
    fireEvent.click(await screen.findByRole("button", { name: "移除" }));

    await waitFor(() => expect(writes).toHaveLength(1));
    expect(JSON.parse(writes[0].body)).toEqual({ schedules: [5] });
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

    expect(await screen.findByTestId("schedule-row-0")).toHaveTextContent(/^每天 00:00 \(UTC\)/);
    expect(screen.getByTestId("schedule-row-1")).toHaveTextContent(/^每天 00:00 \(UTC\)/);
    expect(screen.getByTestId("schedule-row-1")).not.toHaveTextContent("false");
  });

  it("importing a file refreshes the schedules too — a schedules.json is a legitimate import", async () => {
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
    expect(row).toHaveTextContent("每天 09:00");
    expect(row).not.toHaveTextContent("null");
  });

  it("removing tells apart two rows that differ only in the order of a list", async () => {
    // `with: {ids: [1, 2]}` and `with: {ids: [2, 1]}` are two schedules to the
    // sweep (the trigger key fingerprints the payload as written). A set-wise
    // comparison would call them one and remove the first when the second was
    // clicked.
    listMock.mockResolvedValue([]);
    const a = { ...NIGHTLY, index: 0, raw: { every: "hourly", run: "nightly", with: { ids: [1, 2] } } };
    const b = { ...NIGHTLY, index: 1, raw: { every: "hourly", run: "nightly", with: { ids: [2, 1] } } };
    schedulesMock.mockResolvedValue(schedules({ rows: [a, b] }));
    const { svc, writes } = fakeService();
    render(svc);

    fireEvent.click(await screen.findByTestId("schedule-remove-1"));
    fireEvent.click(await screen.findByRole("button", { name: "移除" }));

    await waitFor(() => expect(writes).toHaveLength(1));
    expect(JSON.parse(writes[0].body)).toEqual({
      schedules: [{ every: "hourly", run: "nightly", with: { ids: [1, 2] } }],
    });
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

    expect(await screen.findByTestId("schedule-row-0")).toHaveTextContent(/^每天 00:00 \(UTC\)/);
    expect(screen.getByTestId("schedule-row-1")).toHaveTextContent(/^每天 00:00 \(UTC\)/);
  });

  it("removing asks once, and a cancel writes nothing", async () => {
    listMock.mockResolvedValue([]);
    schedulesMock.mockResolvedValue(schedules());
    const { svc, writes } = fakeService();
    render(svc);

    fireEvent.click(await screen.findByTestId("schedule-remove-0"));
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "移除排程" })).toBeNull());
    expect(writes).toHaveLength(0);
  });
});
