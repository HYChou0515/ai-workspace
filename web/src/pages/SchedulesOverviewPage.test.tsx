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

import { qk } from "../api/queryKeys";
import { ScheduleActionError } from "../api/schedules";
import { translate } from "../lib/i18n";
import { readScheduleOverviewPrefs } from "../lib/scheduleOverviewPrefs";
import { ViewerClockPin } from "../lib/viewerClock";
import { QueryWrap } from "../test/queryWrapper";
import { SchedulesOverviewPage, orderRows, placeOf, refreshEvery } from "./SchedulesOverviewPage";

const word = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);

// The viewer reads in Taipei on Thursday 2026-10-08 at 14:00 — pinned, because
// the machine a test runs on has a zone of its own.
const TPE = "Asia/Taipei";
const NOW = Date.UTC(2026, 9, 8, 6, 0);
const MIN = 60000;

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
  next_ms: NOW + 15 * MIN,
  due_now: false,
  tz: "UTC",
  known: true,
  run_problem: "",
  trigger_id: "wui:i-1:aaaa",
  last_run: null,
  can_edit: true,
  can_run: true,
  can_read: true,
  page_path: "",
  page_title: "",
  run_title: "",
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
      <ViewerClockPin viewer={TPE} now={NOW}>
        <QueryWrap>{children}</QueryWrap>
      </ViewerClockPin>
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
    const failed = row({ run: "failed", last_run: { run_id: "r1", status: "error", started: 5, ended: 6, by_hand: false } });
    const review = row({
      run: "review",
      last_run: { run_id: "r2", status: "awaiting_human", started: 9, ended: null, by_hand: false },
    });
    const fine = row({ run: "fine", next_ms: 2_000, last_run: { run_id: "r3", status: "done", started: 1, ended: 2, by_hand: false } });

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

  it("links the last run to the schedule's own conversation", async () => {
    const ran = row({ last_run: { run_id: "r1", status: "error", started: 1, ended: 2, by_hand: false } });
    render(<SchedulesOverviewPage client={client({ rows: [ran] })} />, { wrapper: Wrap });

    const link = await screen.findByRole("link", { name: new RegExp(word("scheduleOverview.status.error")) });
    expect(link).toHaveAttribute("href", "/a/rca/i-1?chat=wui%3Ai-1%3Aaaaa&from=schedules");
    expect(link).not.toHaveTextContent(word("scheduleOverview.byHand"));
  });

  it("tags a last run started by Run now", async () => {
    const pressed = row({ last_run: { run_id: "r1", status: "done", started: NOW - 5 * MIN, ended: NOW - 4 * MIN, by_hand: true } });
    render(<SchedulesOverviewPage client={client({ rows: [pressed] })} />, { wrapper: Wrap });

    const link = await screen.findByRole("link", { name: new RegExp(word("scheduleOverview.status.done")) });
    expect(link).toHaveTextContent(`${word("scheduleOverview.status.done")}${word("scheduleOverview.byHand")} 4 分鐘前`);
  });

  it("shows where each schedule lives, its next run and that it never ran — by the names people gave", async () => {
    const page = row({
      item_id: "i-2",
      path: "/reports/scrap/schedules.json",
      page_path: "/reports/scrap/page.ai.yaml",
      page_title: "Scrap board",
      trigger_id: "wui:i-2:bbbb",
      run_title: "Daily summary",
    });
    const untitled = row({
      item_id: "i-3",
      path: "/reports/yield/schedules.json",
      trigger_id: "wui:i-3:cccc",
    });
    render(<SchedulesOverviewPage client={client({ rows: [row({}), page, untitled] })} />, { wrapper: Wrap });

    const first = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");
    expect(within(first).getByRole("link", { name: "Line 3" })).toHaveAttribute("href", "/a/rca/i-1");
    expect(first).toHaveTextContent(word("scheduleOverview.itemOwn"));
    expect(within(first).getByText("15 分鐘後")).toBeInTheDocument();
    expect(within(first).getByText(word("scheduleOverview.never"))).toBeInTheDocument();
    // A workflow with no title is named by its id.
    expect(first).toHaveTextContent("report");
    const second = screen.getByTestId("schedule-i-2/reports/scrap/schedules.json#0");
    expect(
      within(second).getByRole("link", { name: word("scheduleOverview.page", { page: "Scrap board" }) }),
    ).toHaveAttribute("href", expect.stringContaining("/w/rca/i-2/reports/scrap/page.ai.yaml"));
    expect(second).toHaveTextContent("Daily summary");
    expect(second).not.toHaveTextContent("report");
    // A page never Deployed (or with no title) is named by its folder.
    const third = screen.getByTestId("schedule-i-3/reports/yield/schedules.json#0");
    expect(third).toHaveTextContent(word("scheduleOverview.page", { page: "/reports/yield" }));
  });

  it("reads every time on the viewer's clock, with no zone label", async () => {
    // A row with no zone is UTC — the sweep's — and a Taipei row beside it:
    // the table used to label one UTC and the other Asia/Taipei.
    const utc = row({
      raw: { every: "daily", at: "09:00", run: "report" },
      next_ms: Date.UTC(2026, 9, 9, 9, 0),
      last_run: { run_id: "r1", status: "done", started: null, ended: Date.UTC(2026, 9, 8, 5, 30), by_hand: false },
    });
    const taipei = row({
      item_id: "i-2",
      trigger_id: "wui:i-2:bbbb",
      tz: TPE,
      raw: { every: "weekly", dow: "mon", at: "08:30", tz: TPE, run: "report" },
      next_ms: Date.UTC(2026, 9, 12, 0, 30),
    });
    render(<SchedulesOverviewPage client={client({ rows: [utc, taipei] })} />, { wrapper: Wrap });

    const first = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");
    expect(within(first).getByText("每天 17:00")).toHaveAttribute("title", "設定為：每天 09:00（世界標準時間）");
    expect(within(first).getByText("明天 17:00")).toHaveAttribute("title", "2026/10/9（週五）17:00");
    expect(within(first).getByRole("link", { name: /30 分鐘前/ })).toBeInTheDocument();
    const second = screen.getByTestId("schedule-i-2/.workflows/schedules.json#0");
    expect(within(second).getByText("每週一 08:30")).toBeInTheDocument();
    expect(within(second).getByText("10/12（週一）08:30")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(table).not.toHaveTextContent("UTC");
    expect(table).not.toHaveTextContent("Asia/Taipei");
  });

  it("says why a schedule will not run where its next run would be", async () => {
    const gone = row({ runnable: false, known: false, next_at: "", next_ms: null });
    render(<SchedulesOverviewPage client={client({ rows: [gone] })} />, { wrapper: Wrap });

    expect(await screen.findByText(word("schedules.unknownWorkflow"))).toBeInTheDocument();
    // …and offers no Run now on it: there is nothing to run (`mayRunNow`).
    expect(screen.queryByRole("button", { name: word("schedules.runNow") })).toBeNull();
  });

  it("says when this system runs no schedules at all", async () => {
    render(<SchedulesOverviewPage client={client({ enabled: false })} />, { wrapper: Wrap });

    expect(await screen.findByText(word("schedules.disabled"))).toBeInTheDocument();
  });

  it("groups by app on request and remembers the choice", async () => {
    const rows = [row({}), row({ slug: "pm", item_id: "p-1", trigger_id: "wui:p-1:cccc" })];
    render(<SchedulesOverviewPage client={client({ rows })} />, { wrapper: Wrap });
    await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");
    expect(screen.queryByRole("columnheader", { name: "根因分析" })).toBeNull();

    fireEvent.change(screen.getByLabelText(word("scheduleOverview.group")), { target: { value: "app" } });

    // ONE table, so the columns line up across groups; a group is a heading row.
    expect(await screen.findByRole("columnheader", { name: "根因分析" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "專案管理" })).toBeInTheDocument();
    expect(screen.getAllByRole("table")).toHaveLength(1);
    expect(readScheduleOverviewPrefs()).toEqual({ group: "app", sort: "next" });
  });

  it("orders by what needs attention on request and remembers the choice", async () => {
    const fine = row({ run: "fine", next_ms: 1 });
    const failed = row({
      run: "failed",
      item_id: "i-9",
      next_ms: 9,
      trigger_id: "wui:i-9:dddd",
      last_run: { run_id: "r", status: "error", started: 1, ended: 2, by_hand: false },
    });
    render(<SchedulesOverviewPage client={client({ rows: [fine, failed] })} />, { wrapper: Wrap });
    await screen.findByTestId("schedule-i-9/.workflows/schedules.json#0");

    fireEvent.change(screen.getByLabelText(word("scheduleOverview.sort")), { target: { value: "trouble" } });

    await waitFor(() => {
      const rows = screen.getAllByRole("row").slice(1);
      expect(rows[0]).toHaveAttribute("data-testid", "schedule-i-9/.workflows/schedules.json#0");
    });
    expect(readScheduleOverviewPrefs().sort).toBe("trouble");
    // Something needs attention, so the order shows it — nothing to explain.
    expect(screen.queryByText(word("scheduleOverview.sort.calm"))).toBeNull();
  });

  it("says so when nothing needs attention, instead of looking like the sort did nothing", async () => {
    render(<SchedulesOverviewPage client={client()} />, { wrapper: Wrap });
    await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    fireEvent.change(screen.getByLabelText(word("scheduleOverview.sort")), { target: { value: "trouble" } });

    expect(await screen.findByText(word("scheduleOverview.sort.calm"))).toBeInTheDocument();
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
    expect(await within(tr).findByRole("status")).toHaveTextContent(word("schedules.started"));
    expect(c.runNow).toHaveBeenCalledWith("rca", "i-1", {
      path: "/.workflows/schedules.json",
      trigger_id: "wui:i-1:aaaa",
    });

    fireEvent.click(within(tr).getByRole("button", { name: word("schedules.runNow") }));
    expect(await within(tr).findByRole("alert")).toHaveTextContent("still going");
  });

  it("leads from Run now to the run, until the row's last run is that run", async () => {
    const before = { enabled: true, rows: [row({})], files: [] };
    const after = {
      ...before,
      rows: [row({ last_run: { run_id: "run-1", status: "running", started: NOW, ended: null, by_hand: true } })],
    };
    const c = client({}, { overview: vi.fn().mockResolvedValueOnce(before).mockResolvedValue(after) });
    render(<SchedulesOverviewPage client={c} />, { wrapper: Wrap });
    const tr = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    fireEvent.click(within(tr).getByRole("button", { name: word("schedules.runNow") }));

    // The refetch after the press already carries the run: the last-run cell
    // says it all, so the note goes.
    const last = await within(tr).findByRole("link", { name: new RegExp(word("scheduleOverview.status.running")) });
    expect(last).toHaveAttribute("href", "/a/rca/i-1?chat=wui%3Ai-1%3Aaaaa&from=schedules");
    await waitFor(() => expect(within(tr).queryByRole("status")).toBeNull());
  });

  it("drops the note once the row shows any newer run — even with no run id back", async () => {
    // Review round 1: the note waited for the row to show THE run Run now
    // started, so a reply without a run id, or a fire that overtook it, left
    // it on screen for good.
    const before = { enabled: true, rows: [row({})], files: [] };
    const after = {
      ...before,
      rows: [row({ last_run: { run_id: "fired", status: "running", started: NOW, ended: null, by_hand: false } })],
    };
    const c = client(
      {},
      {
        overview: vi.fn().mockResolvedValueOnce(before).mockResolvedValue(after),
        runNow: vi.fn(async () => ""),
      },
    );
    render(<SchedulesOverviewPage client={c} />, { wrapper: Wrap });
    const tr = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    fireEvent.click(within(tr).getByRole("button", { name: word("schedules.runNow") }));

    await within(tr).findByRole("link", { name: new RegExp(word("scheduleOverview.status.running")) });
    await waitFor(() => expect(within(tr).queryByRole("status")).toBeNull());
  });

  it("links the note to the run while the row does not show it yet", async () => {
    const c = client();
    render(<SchedulesOverviewPage client={c} />, { wrapper: Wrap });
    const tr = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    fireEvent.click(within(tr).getByRole("button", { name: word("schedules.runNow") }));

    const note = await within(tr).findByRole("status");
    expect(within(note).getByRole("link", { name: word("scheduleOverview.seeRun") })).toHaveAttribute(
      "href",
      "/a/rca/i-1?chat=wui%3Ai-1%3Aaaaa&from=schedules",
    );
  });

  it("asks again on its own while a run on it is going", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const going = row({ last_run: { run_id: "r", status: "running", started: 1, ended: null, by_hand: false } });
      const c = client({ rows: [going] });
      render(<SchedulesOverviewPage client={c} />, { wrapper: Wrap });
      await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");
      expect(c.overview).toHaveBeenCalledTimes(1);

      await vi.advanceTimersByTimeAsync(5500);

      expect(c.overview).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("refreshes every few seconds while a run is going, and every minute otherwise", () => {
    const going = row({ last_run: { run_id: "r", status: "running", started: 1, ended: null, by_hand: false } });
    const done = row({ last_run: { run_id: "r", status: "done", started: 1, ended: 2, by_hand: false } });
    expect(refreshEvery({ enabled: true, rows: [done, going], files: [] })).toBe(5000);
    expect(refreshEvery({ enabled: true, rows: [done, row({})], files: [] })).toBe(60000);
    expect(refreshEvery(undefined)).toBe(60000);
  });

  it("removes a schedule after asking", async () => {
    const c = client();
    render(<SchedulesOverviewPage client={c} />, { wrapper: Wrap });
    const tr = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    const button = within(tr).getByRole("button", { name: new RegExp(`^${word("schedules.remove")}`) });
    expect(button).toHaveAttribute("data-variant", "danger");
    // Named with its place: two rows with the same period and workflow on
    // different items must not read the same to a screen reader.
    expect(button).toHaveAccessibleName(`${word("schedules.remove")} Line 3 · 每天 17:00 → report`);
    fireEvent.click(button);
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(
      word("scheduleOverview.removeConfirm", { place: "Line 3", period: "每天 17:00", workflow: "report" }),
    );
    // The safe answer first.
    expect(within(dialog).getAllByRole("button").map((b) => b.textContent)).toEqual([
      word("schedules.cancel"),
      word("schedules.remove"),
    ]);
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
        index: 0,
      }),
    );
  });

  it("refreshes the item's panel and file list too, not only this page", async () => {
    // Review round 1: the panel lists the same row from a 30s cache; left
    // alone it showed the removed row, and pressing it was a 409.
    const { QueryClient } = await import("@tanstack/react-query");
    const spy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    const c = client();
    render(<SchedulesOverviewPage client={c} />, { wrapper: Wrap });
    const tr = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    fireEvent.click(within(tr).getByRole("button", { name: new RegExp(`^${word("schedules.remove")}`) }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: word("schedules.remove") }));

    await waitFor(() => expect(c.remove).toHaveBeenCalled());
    await waitFor(() => {
      const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
      expect(keys).toEqual(
        expect.arrayContaining([
          JSON.stringify(qk.schedulesOverview),
          JSON.stringify(qk.itemSchedules("rca", "i-1")),
          JSON.stringify(qk.files("i-1")),
          JSON.stringify(qk.itemChats("rca", "i-1")),
        ]),
      );
    });
    spy.mockRestore();
  });

  it("moves a schedule to a new time", async () => {
    const c = client();
    render(<SchedulesOverviewPage client={c} />, { wrapper: Wrap });
    const tr = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    fireEvent.click(within(tr).getByRole("button", { name: word("schedules.editTime") }));
    const modal = await screen.findByTestId("schedule-time-modal");
    expect(within(modal).getByText(word("schedules.edit.note"))).toBeInTheDocument();
    fireEvent.change(within(modal).getByLabelText(word("schedules.edit.hour")), { target: { value: "10" } });
    fireEvent.change(within(modal).getByLabelText(word("schedules.edit.minute")), { target: { value: "30" } });
    fireEvent.click(within(modal).getByTestId("schedule-time-save"));

    await waitFor(() => expect(screen.queryByTestId("schedule-time-modal")).toBeNull());
    expect(c.editTime).toHaveBeenCalledWith(
      "rca",
      "i-1",
      { path: "/.workflows/schedules.json", trigger_id: "wui:i-1:aaaa" },
      expect.objectContaining({ every: "daily", at: "10:30", tz: TPE }),
    );
  });

  it("keeps the edit open with the server's reason when the new time is refused", async () => {
    const c = client({}, { editTime: vi.fn().mockRejectedValue(new ScheduleActionError(422, "at must be HH:MM")) });
    render(<SchedulesOverviewPage client={c} />, { wrapper: Wrap });
    const tr = await screen.findByTestId("schedule-i-1/.workflows/schedules.json#0");

    fireEvent.click(within(tr).getByRole("button", { name: word("schedules.editTime") }));
    const modal = await screen.findByTestId("schedule-time-modal");
    fireEvent.change(within(modal).getByLabelText(word("schedules.edit.hour")), { target: { value: "10" } });
    fireEvent.change(within(modal).getByLabelText(word("schedules.edit.minute")), { target: { value: "30" } });
    fireEvent.click(within(modal).getByTestId("schedule-time-save"));

    expect(await within(modal).findByTestId("schedule-time-error")).toHaveTextContent("at must be HH:MM");
  });
});

describe("placeOf", () => {
  it("names the item, and a page by its title — its folder when it has none", () => {
    expect(placeOf(row({}), word)).toBe("Line 3");
    expect(placeOf(row({ path: "/r/s/schedules.json", page_title: "Board" }), word)).toBe("Line 3 · 頁面「Board」");
    expect(placeOf(row({ path: "/r/s/schedules.json" }), word)).toBe("Line 3 · 頁面「/r/s」");
  });
});
