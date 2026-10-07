// @vitest-environment happy-dom
/**
 * The schedules overview (`docs/plan-schedule-overview.md`): every schedule the
 * viewer may read, its last run and its next, arranged as the viewer chooses,
 * and the four acts on a row.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OverviewRow, ScheduleOverview, SchedulesApi } from "../api/schedules";

vi.mock("../api", () => ({
  api: {
    listApps: vi.fn(async () => [
      { slug: "rca", title: "根因分析", description: "", icon: "flame", color: "#F0502E" },
      { slug: "pm", title: "專案管理", description: "", icon: "kanban", color: "#3B82F6" },
    ]),
    getUsers: vi.fn(async () => []),
  },
}));

import { ScheduleActionError } from "../api/schedules";
import { translate } from "../lib/i18n";
import { readScheduleOverviewPrefs } from "../lib/scheduleOverviewPrefs";
import { QueryWrap } from "../test/queryWrapper";
import { SchedulesOverviewPage, orderRows } from "./SchedulesOverviewPage";

const word = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);

const row = (over: Partial<OverviewRow>): OverviewRow => ({
  slug: "rca",
  item_id: "i-1",
  item_title: "Line 3",
  item_owner: "bob",
  path: "/.workflows/schedules.json",
  index: 0,
  raw: { every: "daily", at: "09:00", run: "report" },
  problems: [],
  run: "report",
  describe: "daily at 09:00 UTC",
  runnable: true,
  next_at: "2026-10-08 09:00",
  next_ms: 2_000,
  due_now: false,
  tz: "UTC",
  known: true,
  run_problem: "",
  trigger_id: "wui:i-1:aaaa",
  last_run: null,
  can_edit: true,
  can_run: true,
  page_path: "",
  ...over,
});

function client(data: Partial<ScheduleOverview> = {}, over: Partial<SchedulesApi> = {}): SchedulesApi {
  return {
    list: vi.fn(),
    overview: vi.fn(async () => ({ enabled: true, rows: [row({})], files: [], ...data })),
    editTime: vi.fn(async () => {}),
    remove: vi.fn(async () => {}),
    runNow: vi.fn(async () => "run-1"),
    ...over,
  } as SchedulesApi;
}

function Wrap({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter>
      <QueryWrap>{children}</QueryWrap>
    </MemoryRouter>
  );
}

afterEach(cleanup);
beforeEach(() => localStorage.clear());

describe("orderRows", () => {
  const taipei = row({ run: "tw", next_at: "2026-10-08 09:00", tz: "Asia/Taipei", next_ms: 1_000 });
  const utc = row({ run: "utc", next_at: "2026-10-08 09:00", tz: "UTC", next_ms: 29_801_000 });
  const broken = row({ run: "gone", runnable: false, known: false, next_at: "", next_ms: null });

  it("puts what runs soonest first, comparing zones as one instant, and what will not run last", () => {
    expect(orderRows([broken, utc, taipei], "next").map((r) => r.run)).toEqual(["tw", "utc", "gone"]);
  });

  it("puts last runs that need somebody first, most recent first, then the rest by next run", () => {
    const failed = row({ run: "failed", last_run: { run_id: "r1", status: "error", started: 5, ended: 6 } });
    const review = row({
      run: "review",
      last_run: { run_id: "r2", status: "awaiting_human", started: 9, ended: null },
    });
    const fine = row({ run: "fine", last_run: { run_id: "r3", status: "done", started: 1, ended: 2 } });

    expect(orderRows([fine, utc, failed, review, taipei], "trouble").map((r) => r.run)).toEqual([
      "review",
      "failed",
      "tw",
      "fine",
      "utc",
    ]);
  });
});

describe("SchedulesOverviewPage", () => {
  it("says what the page is for when nothing is scheduled", async () => {
    render(<SchedulesOverviewPage client={client({ rows: [] })} />, { wrapper: Wrap });

    expect(await screen.findByText(word("scheduleOverview.empty"))).toBeInTheDocument();
  });

  it("shows where each schedule lives, its next run and that it never ran", async () => {
    const page = row({
      item_id: "i-2",
      path: "/reports/scrap/schedules.json",
      page_path: "/reports/scrap/page.ai.yaml",
      trigger_id: "wui:i-2:bbbb",
    });
    render(<SchedulesOverviewPage client={client({ rows: [row({}), page] })} />, { wrapper: Wrap });

    const first = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");
    expect(within(first).getByRole("link", { name: "Line 3" })).toHaveAttribute("href", "/a/rca/i-1");
    expect(within(first).getByText("2026-10-08 09:00 UTC")).toBeInTheDocument();
    expect(within(first).getByText(word("scheduleOverview.never"))).toBeInTheDocument();
    const second = screen.getByTestId("schedule-i-2/reports/scrap/schedules.json#0");
    expect(
      within(second).getByRole("link", { name: word("scheduleOverview.page", { folder: "/reports/scrap" }) }),
    ).toHaveAttribute("href", expect.stringContaining("/w/rca/i-2/reports/scrap/page.ai.yaml"));
  });

  it("links the last run to the schedule's own conversation", async () => {
    const ran = row({ last_run: { run_id: "r1", status: "error", started: 1, ended: 2 } });
    render(<SchedulesOverviewPage client={client({ rows: [ran] })} />, { wrapper: Wrap });

    const link = await screen.findByRole("link", { name: new RegExp(word("scheduleOverview.status.error")) });
    expect(link).toHaveAttribute("href", "/a/rca/i-1?chat=wui%3Ai-1%3Aaaaa");
  });

  it("says why a schedule will not run where its next run would be", async () => {
    const gone = row({ runnable: false, known: false, next_at: "", next_ms: null });
    render(<SchedulesOverviewPage client={client({ rows: [gone] })} />, { wrapper: Wrap });

    expect(await screen.findByText(word("schedules.unknownWorkflow"))).toBeInTheDocument();
  });

  it("says when this system runs no schedules at all", async () => {
    render(<SchedulesOverviewPage client={client({ enabled: false })} />, { wrapper: Wrap });

    expect(await screen.findByText(word("schedules.disabled"))).toBeInTheDocument();
  });

  it("groups by app on request and remembers the choice", async () => {
    const rows = [row({}), row({ slug: "pm", item_id: "p-1", trigger_id: "wui:p-1:cccc" })];
    render(<SchedulesOverviewPage client={client({ rows })} />, { wrapper: Wrap });
    await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");
    expect(screen.queryByRole("region")).toBeNull();

    fireEvent.change(screen.getByLabelText(word("scheduleOverview.group")), { target: { value: "app" } });

    expect(await screen.findByRole("region", { name: "根因分析" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "專案管理" })).toBeInTheDocument();
    expect(readScheduleOverviewPrefs()).toEqual({ group: "app", sort: "next" });
  });

  it("orders by what needs attention on request and remembers the choice", async () => {
    const fine = row({ run: "fine", next_ms: 1 });
    const failed = row({
      run: "failed",
      item_id: "i-9",
      next_ms: 9,
      trigger_id: "wui:i-9:dddd",
      last_run: { run_id: "r", status: "error", started: 1, ended: 2 },
    });
    render(<SchedulesOverviewPage client={client({ rows: [fine, failed] })} />, { wrapper: Wrap });
    await screen.findByTestId("schedule-i-9/.workflows/schedules.json#0");

    fireEvent.change(screen.getByLabelText(word("scheduleOverview.sort")), { target: { value: "trouble" } });

    await waitFor(() => {
      const rows = screen.getAllByRole("row").slice(1);
      expect(rows[0]).toHaveAttribute("data-testid", "schedule-i-9/.workflows/schedules.json#0");
    });
    expect(readScheduleOverviewPrefs().sort).toBe("trouble");
  });

  it("offers a reader no way to change or run a schedule", async () => {
    render(<SchedulesOverviewPage client={client({ rows: [row({ can_edit: false, can_run: false })] })} />, {
      wrapper: Wrap,
    });
    const tr = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    expect(within(tr).queryAllByRole("button")).toHaveLength(0);
  });

  it("runs a schedule now and says so — or says why not", async () => {
    const c = client(
      {},
      {
        runNow: vi
          .fn()
          .mockResolvedValueOnce("run-1")
          .mockRejectedValueOnce(new ScheduleActionError(409, "still going")),
      },
    );
    render(<SchedulesOverviewPage client={c} />, { wrapper: Wrap });
    const tr = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    fireEvent.click(within(tr).getByRole("button", { name: word("schedules.runNow") }));
    expect(await within(tr).findByText(word("schedules.started"))).toBeInTheDocument();
    expect(c.runNow).toHaveBeenCalledWith("rca", "i-1", {
      path: "/.workflows/schedules.json",
      trigger_id: "wui:i-1:aaaa",
    });

    fireEvent.click(within(tr).getByRole("button", { name: word("schedules.runNow") }));
    expect(await within(tr).findByRole("alert")).toHaveTextContent("still going");
  });

  it("removes a schedule after asking", async () => {
    const c = client();
    render(<SchedulesOverviewPage client={c} />, { wrapper: Wrap });
    const tr = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    fireEvent.click(within(tr).getByRole("button", { name: new RegExp(`^${word("schedules.remove")}`) }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: word("schedules.remove") }));

    await waitFor(() =>
      expect(c.remove).toHaveBeenCalledWith("rca", "i-1", {
        path: "/.workflows/schedules.json",
        trigger_id: "wui:i-1:aaaa",
      }),
    );
  });

  it("offers only Remove on a row the sweep refuses, and sends what it says", async () => {
    const refused = row({ raw: { every: "fortnightly", run: "report" }, trigger_id: "", runnable: false, problems: ["bad"] });
    const c = client({ rows: [refused] });
    render(<SchedulesOverviewPage client={c} />, { wrapper: Wrap });
    const tr = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    expect(within(tr).queryByRole("button", { name: word("schedules.runNow") })).toBeNull();
    expect(within(tr).queryByRole("button", { name: word("schedules.editTime") })).toBeNull();
    fireEvent.click(within(tr).getByRole("button", { name: new RegExp(`^${word("schedules.remove")}`) }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: word("schedules.remove") }));

    await waitFor(() =>
      expect(c.remove).toHaveBeenCalledWith("rca", "i-1", {
        path: "/.workflows/schedules.json",
        trigger_id: "",
        raw: { every: "fortnightly", run: "report" },
      }),
    );
  });

  it("moves a schedule to a new time", async () => {
    const c = client();
    render(<SchedulesOverviewPage client={c} />, { wrapper: Wrap });
    const tr = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    fireEvent.click(within(tr).getByRole("button", { name: word("schedules.editTime") }));
    const modal = await screen.findByTestId("schedule-time-modal");
    expect(within(modal).getByText(word("schedules.edit.note"))).toBeInTheDocument();
    fireEvent.change(within(modal).getByLabelText(word("schedules.edit.at")), { target: { value: "10:30" } });
    fireEvent.click(within(modal).getByTestId("schedule-time-save"));

    await waitFor(() => expect(screen.queryByTestId("schedule-time-modal")).toBeNull());
    expect(c.editTime).toHaveBeenCalledWith(
      "rca",
      "i-1",
      { path: "/.workflows/schedules.json", trigger_id: "wui:i-1:aaaa" },
      expect.objectContaining({ every: "daily", at: "10:30" }),
    );
  });

  it("keeps the edit open with the server's reason when the new time is refused", async () => {
    const c = client({}, { editTime: vi.fn().mockRejectedValue(new ScheduleActionError(422, "at must be HH:MM")) });
    render(<SchedulesOverviewPage client={c} />, { wrapper: Wrap });
    const tr = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    fireEvent.click(within(tr).getByRole("button", { name: word("schedules.editTime") }));
    const modal = await screen.findByTestId("schedule-time-modal");
    fireEvent.change(within(modal).getByLabelText(word("schedules.edit.at")), { target: { value: "10:30" } });
    fireEvent.click(within(modal).getByTestId("schedule-time-save"));

    expect(await within(modal).findByTestId("schedule-time-error")).toHaveTextContent("at must be HH:MM");
  });
});
