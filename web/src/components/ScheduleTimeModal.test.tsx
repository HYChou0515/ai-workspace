// @vitest-environment happy-dom
/**
 * Editing a schedule's time holds unsaved work, so its deliberate exits ask
 * once — and only when something changed against what it opened with
 * (CLAUDE.md, "Leaving a modal"). Both cases, as the rule requires.
 *
 * It speaks the viewer's clock (`docs/plan-schedule-overview-polish.md`
 * decision 6): the row opens moved into their zone, the time is 24-hour, the
 * zone is picked by name. The viewer's zone and `now` are pinned — the machine
 * a test runs on has a zone of its own.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { translate } from "../lib/i18n";
import { ViewerClockPin } from "../lib/viewerClock";
import { renderWithQuery } from "../test/queryWrapper";
import { ScheduleTimeModal, cronToSimple, simpleToCron, timeOf } from "./ScheduleTimeModal";

const word = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);
const REF = { path: "/.workflows/schedules.json", trigger_id: "wui:i:a" };
const TPE = "Asia/Taipei";
// Thursday 2026-10-08 14:00 in Taipei.
const NOW = Date.UTC(2026, 9, 8, 6, 0);
const FRIDAY_1730_TPE = { every: "weekly", dow: "fri", at: "17:30", tz: TPE, run: "r" };

afterEach(cleanup);

function open(raw: unknown = FRIDAY_1730_TPE, viewer = TPE) {
  const onClose = vi.fn();
  const onSave = vi.fn(async () => {});
  renderWithQuery(
    <ViewerClockPin viewer={viewer} now={NOW}>
      <ScheduleTimeModal raw={raw} rowRef={REF} onSave={onSave} onSaved={vi.fn()} onClose={onClose} />
    </ViewerClockPin>,
  );
  return { onClose, onSave };
}

const value = (key: Parameters<typeof translate>[1]) => screen.getByLabelText(word(key));
/** The zone the form's time is in — said in grey, not picked (user, 2026-10-08). */
const zone = () => screen.getByTestId("schedule-time-zone");

describe("ScheduleTimeModal", () => {
  it("opens on the row as written when the viewer is in its zone", () => {
    open();
    expect(value("schedules.edit.every")).toHaveValue("weekly");
    expect(value("schedules.edit.dow")).toHaveValue("fri");
    expect(value("schedules.edit.hour")).toHaveValue("17");
    expect(value("schedules.edit.minute")).toHaveValue("30");
    expect(zone()).toHaveTextContent(word("schedules.edit.zoneYours", { zone: "台北標準時間" }));
  });

  it("opens on the row moved onto the viewer's clock, in the viewer's zone", () => {
    open(FRIDAY_1730_TPE, "UTC");
    expect(value("schedules.edit.dow")).toHaveValue("fri");
    expect(value("schedules.edit.hour")).toHaveValue("09");
    expect(zone()).toHaveTextContent(word("schedules.edit.zoneYours", { zone: "世界標準時間" }));
  });

  it("keeps a time that would land on another day of the month in its own zone", () => {
    open({ every: "monthly", dom: 1, at: "02:00", tz: TPE, run: "r" }, "UTC");
    expect(value("schedules.edit.hour")).toHaveValue("02");
    // Not the viewer's zone, so it is named without "your" — the time is read there.
    expect(zone()).toHaveTextContent(word("schedules.edit.zone", { zone: "台北標準時間" }));
    expect(zone()).not.toHaveTextContent("你的時區");
  });

  it("writes the time in 24 hours, never 上午/下午", () => {
    open();
    const hours = within(value("schedules.edit.hour")).getAllByRole("option").map((o) => o.textContent);
    expect(hours).toHaveLength(24);
    expect(hours[0]).toBe("00");
    expect(hours[23]).toBe("23");
  });

  it("says the zone in grey and offers nothing to pick — never an id", () => {
    open({ every: "daily", at: "09:00", run: "r" });
    expect(zone()).toHaveTextContent("台北標準時間");
    expect(zone()).not.toHaveTextContent("Asia/Taipei");
    expect(zone().tagName).not.toBe("SELECT");
    expect(screen.getAllByRole("combobox").map((c) => c.getAttribute("aria-label"))).toEqual([
      word("schedules.edit.every"),
      word("schedules.edit.hour"),
      word("schedules.edit.minute"),
    ]);
  });

  it("saves in the zone it shows — the viewer's when the time moved there", async () => {
    const { onSave } = open({ every: "daily", at: "09:00", run: "r" });
    // 09:00 UTC opened as 17:00 in Taipei.
    expect(value("schedules.edit.hour")).toHaveValue("17");
    fireEvent.change(value("schedules.edit.hour"), { target: { value: "18" } });
    fireEvent.click(screen.getByTestId("schedule-time-save"));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(REF, expect.objectContaining({ every: "daily", at: "18:00", tz: TPE })),
    );
  });

  it("puts focus on its title, not on a field — a focused field reads as an error", () => {
    open();
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: word("schedules.edit.title") }));
  });

  it("closes at once when nothing changed — opening it moved nothing the reader did", () => {
    const { onClose } = open({ every: "daily", at: "09:00", run: "r" });
    fireEvent.click(screen.getByRole("button", { name: word("schedules.cancel") }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(word("dirtyClose.prompt"))).toBeNull();
  });

  it("asks once before throwing a changed time away", async () => {
    const { onClose } = open();
    fireEvent.change(value("schedules.edit.hour"), { target: { value: "08" } });
    fireEvent.click(screen.getByRole("button", { name: word("schedules.cancel") }));

    const ask = await screen.findByRole("dialog", { name: word("dirtyClose.prompt") });
    fireEvent.click(within(ask).getByRole("button", { name: word("dirtyClose.keep") }));
    await waitFor(() => expect(screen.queryByText(word("dirtyClose.prompt"))).toBeNull());
    expect(onClose).not.toHaveBeenCalled();
  });

  it("does not count switching the period away and back as a change", () => {
    const { onClose } = open();
    const every = value("schedules.edit.every");
    fireEvent.change(every, { target: { value: "monthly" } });
    fireEvent.change(every, { target: { value: "weekly" } });
    fireEvent.click(screen.getByRole("button", { name: word("schedules.cancel") }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("reads a row that leaves fields out with the parser's defaults — 00:00 UTC", () => {
    expect(timeOf({ run: "r" }, "UTC", NOW)).toEqual({
      every: "daily",
      n: 15,
      at: "00:00",
      dow: "mon",
      dom: 1,
      tz: "UTC",
    });
    expect(timeOf({ run: "r" }, TPE, NOW)).toMatchObject({ at: "08:00", tz: TPE });
    expect(timeOf("not a row", TPE, NOW).every).toBe("daily");
    // The sweep accepts an unpadded hour (`_looks_like_time`); the form must
    // open on that time, not on 00:00 — saving would move the schedule.
    expect(timeOf({ every: "daily", at: "9:05", run: "r" }, "UTC", NOW).at).toBe("09:05");
    expect(timeOf({ every: "daily", at: "9:05", tz: TPE, run: "r" }, TPE, NOW).at).toBe("09:05");
    // An unpadded minute too, and moved like any other time (review round 2).
    expect(timeOf({ every: "daily", at: "9:5", run: "r" }, TPE, NOW)).toMatchObject({ at: "17:05", tz: TPE });
    // An hourly row moves to the viewer's zone only when its firing minute
    // stays put: not from UTC to Kolkata (+05:30).
    expect(timeOf({ every: "hourly", run: "r" }, TPE, NOW).tz).toBe(TPE);
    expect(timeOf({ every: "hourly", run: "r" }, "Asia/Kolkata", NOW).tz).toBe("UTC");
  });
});


describe("ScheduleTimeModal — 簡單 / cron (docs/plan-schedule-cron.md decision 4)", () => {
  const WEEKDAYS = { cron: "0 9 * * 1-5", tz: TPE, run: "r" };
  const mode = (name: string) => screen.getByRole("tab", { name });
  const cronBox = () => screen.getByRole("textbox", { name: word("schedules.edit.cronField") });

  it("opens a cron row in cron mode, its words under it, its zone in grey", () => {
    open(WEEKDAYS);
    expect(mode(word("schedules.edit.mode.cron"))).toHaveAttribute("aria-selected", "true");
    expect(cronBox()).toHaveValue("0 9 * * 1-5");
    expect(screen.getByTestId("schedule-cron-words")).toHaveTextContent("在 09:00, 星期一 到 星期五");
    expect(zone()).toHaveTextContent(word("schedules.edit.zoneYours", { zone: "台北標準時間" }));
  });

  it("switches mode with tabs, not buttons — the textbook tab, as the share dialog draws it", () => {
    // User, 2026-10-08: 「你的tab有問題 看起來像按鈕」. Two filled buttons read
    // as actions to press; a mode switch is a tab strip (WAI-ARIA tabs; Material
    // 3 secondary tabs: text with an underline, no fill).
    open(WEEKDAYS);
    const strip = screen.getByRole("tablist", { name: word("schedules.edit.mode") });
    const tabs = within(strip).getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      word("schedules.edit.mode.simple"),
      word("schedules.edit.mode.cron"),
    ]);
    for (const tab of tabs) {
      expect(tab).not.toHaveClass("btn");
      expect(tab).not.toHaveAttribute("data-variant");
    }
    // The fields below are the selected tab's panel.
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAttribute("aria-labelledby", mode(word("schedules.edit.mode.cron")).id);
    expect(within(panel).getByRole("textbox", { name: word("schedules.edit.cronField") })).toBeInTheDocument();
  });

  it("opens an every row in 簡單 mode", () => {
    open();
    expect(mode(word("schedules.edit.mode.simple"))).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("textbox", { name: word("schedules.edit.cronField") })).toBeNull();
  });

  it("switching 簡單 → cron fills the same time as a cron", () => {
    open({ every: "weekly", dow: "mon", at: "08:30", tz: TPE, run: "r" });
    fireEvent.click(mode(word("schedules.edit.mode.cron")));
    expect(cronBox()).toHaveValue("30 8 * * 1");
  });

  it("a cron it cannot follow says why and cannot be saved", () => {
    open(WEEKDAYS);
    for (const bad of ["0 9 * *", "0 0 9 * * 1-5", "0 25 * * *"]) {
      fireEvent.change(cronBox(), { target: { value: bad } });
      expect(screen.getByTestId("schedule-cron-error")).toBeInTheDocument();
      expect(screen.getByTestId("schedule-time-save")).toBeDisabled();
    }
  });

  it("saves a cron as a cron — no `every` beside it", async () => {
    const { onSave } = open(WEEKDAYS);
    fireEvent.change(cronBox(), { target: { value: "0 9 * * 1-6" } });
    fireEvent.click(screen.getByTestId("schedule-time-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith(REF, { cron: "0 9 * * 1-6", tz: TPE }));
  });

  it("switching cron → 簡單 carries a cron the simple form can say", () => {
    open({ cron: "30 8 * * 1", tz: TPE, run: "r" });
    fireEvent.click(mode(word("schedules.edit.mode.simple")));
    expect(value("schedules.edit.every")).toHaveValue("weekly");
    expect(value("schedules.edit.dow")).toHaveValue("mon");
    expect(value("schedules.edit.hour")).toHaveValue("08");
    expect(screen.queryByText(word("schedules.edit.replacesCron"))).toBeNull();
  });

  it("switching cron → 簡單 on one it cannot say starts over and says the cron goes", () => {
    open(WEEKDAYS);
    fireEvent.click(mode(word("schedules.edit.mode.simple")));
    expect(screen.getByText(word("schedules.edit.replacesCron"))).toBeInTheDocument();
  });

  it("a month day the cron would skip in short months says so", () => {
    open({ every: "monthly", dom: 31, at: "09:00", tz: TPE, run: "r" });
    fireEvent.click(mode(word("schedules.edit.mode.cron")));
    expect(cronBox()).toHaveValue("0 9 31 * *");
    expect(screen.getByText(word("schedules.edit.cronSkipsMonths"))).toBeInTheDocument();
  });

  it("asks once before throwing an edited cron away", async () => {
    const { onClose } = open(WEEKDAYS);
    fireEvent.change(cronBox(), { target: { value: "0 9 * * 1-6" } });
    fireEvent.click(screen.getByRole("button", { name: word("schedules.cancel") }));

    const ask = await screen.findByRole("dialog", { name: word("dirtyClose.prompt") });
    fireEvent.click(within(ask).getByRole("button", { name: word("dirtyClose.keep") }));
    await waitFor(() => expect(screen.queryByText(word("dirtyClose.prompt"))).toBeNull());
    expect(onClose).not.toHaveBeenCalled();
    expect(cronBox()).toHaveValue("0 9 * * 1-6");
  });

  it("switching away and back with nothing changed is not a change — and keeps the cron as typed", () => {
    const { onClose } = open(WEEKDAYS);
    fireEvent.click(mode(word("schedules.edit.mode.simple")));
    fireEvent.click(mode(word("schedules.edit.mode.cron")));
    expect(cronBox()).toHaveValue("0 9 * * 1-5");
    fireEvent.click(screen.getByRole("button", { name: word("schedules.cancel") }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("converts both ways for every shape the simple form has", () => {
    const shapes = [
      [{ every: "minutes", n: 15 }, "*/15 * * * *"],
      [{ every: "minutes", n: 1 }, "* * * * *"],
      [{ every: "hourly" }, "0 * * * *"],
      [{ every: "daily", at: "09:05" }, "5 9 * * *"],
      [{ every: "weekly", dow: "sun", at: "23:00" }, "0 23 * * 0"],
      [{ every: "monthly", dom: 15, at: "07:30" }, "30 7 15 * *"],
    ] as const;
    for (const [simple, cron] of shapes) {
      expect(simpleToCron({ tz: TPE, ...simple })).toBe(cron);
      expect(cronToSimple(cron)).toMatchObject(simple);
    }
    // Every 60 minutes is the top of the hour — `*/60` is not a minute a cron has.
    expect(simpleToCron({ every: "minutes", n: 60, tz: TPE })).toBe("0 * * * *");
    expect(cronToSimple("0 9 * * 1-5")).toBeNull();
    expect(cronToSimple("*/7 * * * *")).toBeNull();
  });
});
