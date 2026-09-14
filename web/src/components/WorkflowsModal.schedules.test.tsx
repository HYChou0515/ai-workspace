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
  payload: {},
};

function schedules(over: Partial<ItemSchedules> = {}): ItemSchedules {
  return { enabled: true, path: ".workflows/schedules.json", rows: [NIGHTLY], problems: [], ...over };
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
      schedules({ rows: [{ ...NIGHTLY, run: "vanished", known: false }] }),
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
      schedules({ rows: [{ ...NIGHTLY, next_at: "", due_now: true }] }),
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
