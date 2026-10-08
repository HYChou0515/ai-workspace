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
import { ScheduleTimeModal, timeOf } from "./ScheduleTimeModal";

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

describe("ScheduleTimeModal", () => {
  it("opens on the row as written when the viewer is in its zone", () => {
    open();
    expect(value("schedules.edit.every")).toHaveValue("weekly");
    expect(value("schedules.edit.dow")).toHaveValue("fri");
    expect(value("schedules.edit.hour")).toHaveValue("17");
    expect(value("schedules.edit.minute")).toHaveValue("30");
    expect(value("schedules.edit.tz")).toHaveValue(TPE);
  });

  it("opens on the row moved onto the viewer's clock, in the viewer's zone", () => {
    open(FRIDAY_1730_TPE, "UTC");
    expect(value("schedules.edit.dow")).toHaveValue("fri");
    expect(value("schedules.edit.hour")).toHaveValue("09");
    expect(value("schedules.edit.tz")).toHaveValue("UTC");
  });

  it("keeps a time that would land on another day of the month in its own zone", () => {
    open({ every: "monthly", dom: 1, at: "02:00", tz: TPE, run: "r" }, "UTC");
    expect(value("schedules.edit.hour")).toHaveValue("02");
    expect(value("schedules.edit.tz")).toHaveValue(TPE);
  });

  it("writes the time in 24 hours, never 上午/下午", () => {
    open();
    const hours = within(value("schedules.edit.hour")).getAllByRole("option").map((o) => o.textContent);
    expect(hours).toHaveLength(24);
    expect(hours[0]).toBe("00");
    expect(hours[23]).toBe("23");
  });

  it("offers zones by name — the viewer's first — never as an id to type", () => {
    open({ every: "daily", at: "09:00", run: "r" });
    const zone = value("schedules.edit.tz");
    expect(zone.tagName).toBe("SELECT");
    const [mine, second] = within(zone).getAllByRole("option");
    expect(mine).toHaveTextContent(word("schedules.edit.yourZone", { zone: "台北標準時間" }));
    expect(second).toHaveTextContent("世界標準時間");
  });

  it("saves in the zone chosen — the viewer's by default", async () => {
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
