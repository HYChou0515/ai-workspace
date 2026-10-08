import { describe, expect, it } from "vitest";

import { translate } from "./i18n";
import { fullTime, moveTime, periodText, wallOf, whenText, zonedMs, zoneName } from "./scheduleTime";

const t = (key: Parameters<typeof translate>[1], vars?: Parameters<typeof translate>[2]) =>
  translate("zh-TW", key, vars);
const en = (key: Parameters<typeof translate>[1], vars?: Parameters<typeof translate>[2]) =>
  translate("en", key, vars);

// Thursday 2026-10-08 14:00 in Taipei.
const NOW = Date.UTC(2026, 9, 8, 6, 0);
const TPE = "Asia/Taipei";
const MIN = 60000;

describe("whenText — an instant on the viewer's clock", () => {
  it.each([
    [NOW + 15 * MIN, "15 分鐘後"],
    [NOW + 20000, "不到 1 分鐘後"],
    [NOW - 3 * MIN, "3 分鐘前"],
    [NOW - 10000, "剛剛"],
    [Date.UTC(2026, 9, 8, 15, 34), "今天 23:34"],
    [Date.UTC(2026, 9, 9, 1, 0), "明天 09:00"],
    [Date.UTC(2026, 9, 7, 1, 0), "昨天 09:00"],
    [Date.UTC(2026, 9, 12, 0, 30), "10/12（週一）08:30"],
    [Date.UTC(2027, 0, 5, 1, 0), "2027/1/5（週二）09:00"],
  ])("%s reads %s in Taipei", (ms, words) => {
    expect(whenText(ms, NOW, TPE, t)).toBe(words);
  });

  it("is the viewer's day, not UTC's — 01:00 UTC is already tomorrow in Taipei", () => {
    const ms = Date.UTC(2026, 9, 9, 1, 0);
    expect(whenText(ms, NOW, TPE, t)).toBe("明天 09:00");
    expect(whenText(ms, NOW, "UTC", t)).toBe("明天 01:00");
    // 23:00 on the 7th in Los Angeles: its tomorrow is the 8th.
    expect(whenText(ms, NOW, "America/Los_Angeles", t)).toBe("明天 18:00");
  });

  it("a past event never reads as ahead, a coming one never as gone", () => {
    // The page's `now` moves every 30 s; a run that started after the last
    // tick read "不到 1 分鐘後" in the recorded demo, and a next run the sweep
    // has not picked up yet would read "3 分鐘前".
    expect(whenText(NOW + 20000, NOW, TPE, t, "past")).toBe("剛剛");
    expect(whenText(NOW - 3 * MIN, NOW, TPE, t, "future")).toBe("不到 1 分鐘後");
    expect(whenText(NOW - 3 * MIN, NOW, TPE, t, "past")).toBe("3 分鐘前");
    // A server clock well ahead of the browser's: still just now, not "今天 16:00".
    expect(whenText(NOW + 120 * MIN, NOW, TPE, t, "past")).toBe("剛剛");
  });

  it("the hover has the whole date", () => {
    expect(fullTime(Date.UTC(2026, 9, 8, 15, 34), TPE, t)).toBe("2026/10/8（週四）23:34");
    expect(fullTime(Date.UTC(2026, 9, 8, 15, 34), TPE, en)).toBe("Thu 2026/10/8 23:34");
  });
});

describe("periodText — a row's period on the viewer's clock", () => {
  const at = (viewer: string, nextMs?: number) => ({ viewer, now: NOW, nextMs, locale: "zh-TW" });

  it("a row with no zone is UTC — what the sweep fires it by — and is moved like any", () => {
    expect(periodText({ every: "daily", at: "09:00" }, at(TPE), t)).toEqual({
      text: "每天 17:00",
      set: "設定為：每天 09:00（世界標準時間）",
    });
  });

  it("a row already in the viewer's zone reads as written", () => {
    expect(periodText({ every: "daily", at: "09:00", tz: TPE }, at(TPE), t).text).toBe("每天 09:00");
  });

  it("a weekly that crosses midnight moves its weekday", () => {
    const row = { every: "weekly", dow: "mon", at: "02:00", tz: TPE };
    expect(periodText(row, at("UTC"), t).text).toBe("每週日 18:00");
    expect(periodText({ ...row, at: "08:30" }, at(TPE), t).text).toBe("每週一 08:30");
  });

  it("a monthly that would land on another day keeps its own zone, named in words", () => {
    const row = { every: "monthly", dom: 1, at: "02:00", tz: TPE };
    expect(periodText(row, at("UTC"), t).text).toBe("每月 1 日 02:00（台北標準時間）");
    expect(periodText({ ...row, dom: 15, at: "10:00" }, at("UTC"), t).text).toBe("每月 15 日 02:00");
  });

  it("minutes and hourly read the same everywhere — no zone, nothing to add", () => {
    expect(periodText({ every: "minutes", n: 1 }, at(TPE), t)).toEqual({ text: "每分鐘", set: "" });
    expect(periodText({ every: "minutes", n: 15, tz: "UTC" }, at(TPE), t).text).toBe("每 15 分鐘");
    expect(periodText({ every: "hourly", tz: TPE }, at("UTC"), t)).toEqual({ text: "每小時", set: "" });
  });

  it("an hourly or minutes row whose firing minute would move keeps its zone", () => {
    // Hourly fires at the top of the hour in the row's zone: a UTC hourly runs
    // at :30 in Kolkata (+05:30), so "每小時" there would be a different rule.
    const kolkata = { viewer: "Asia/Kolkata", now: NOW, locale: "zh-TW" };
    expect(periodText({ every: "hourly" }, kolkata, t)).toEqual({
      text: "每小時（世界標準時間）",
      set: "設定為：每小時（世界標準時間）",
    });
    // Every 15 minutes is the same set of minutes 5h30m away; every 20 is not.
    expect(periodText({ every: "minutes", n: 15 }, kolkata, t).text).toBe("每 15 分鐘");
    expect(periodText({ every: "minutes", n: 20 }, kolkata, t).text).toBe("每 20 分鐘（世界標準時間）");
  });

  it("reads a time the way the sweep does — digits either side, unpadded too", () => {
    // `_looks_like_time`: "9:5" fires at 09:05 (review round 2).
    expect(periodText({ every: "daily", at: "9:5" }, at(TPE), t).text).toBe("每天 17:05");
    expect(periodText({ every: "daily", at: "009:05" }, at(TPE), t).text).toBe("每天 17:05");
  });

  it("an hourly row moves only when it fires on the same minutes all year", () => {
    // Lord Howe shifts by 30 minutes for summer: a whole hour from UTC in
    // January, half an hour in July — so a UTC hourly is :00 there half the
    // year and :30 the other half (review round 2).
    const january = { viewer: "Australia/Lord_Howe", now: Date.UTC(2026, 0, 15), locale: "zh-TW" };
    expect(periodText({ every: "hourly" }, january, t).text).toBe("每小時（世界標準時間）");
  });

  it("a zone the browser cannot read stays as written", () => {
    expect(periodText({ every: "daily", at: "09:00", tz: "Mars/Base" }, at(TPE), t).text).toBe(
      "每天 09:00（Mars/Base）",
    );
  });

  it("daylight saving is the one in force when it next runs", () => {
    const row = { every: "daily", at: "09:00", tz: "America/New_York" };
    expect(periodText(row, at(TPE, Date.UTC(2026, 6, 1, 13, 0)), t).text).toBe("每天 21:00");
    expect(periodText(row, at(TPE, Date.UTC(2026, 11, 1, 14, 0)), t).text).toBe("每天 22:00");
  });
});

describe("periodText — a cron row (docs/plan-schedule-cron.md decision 3)", () => {
  const at = (viewer: string) => ({ viewer, now: NOW, locale: "zh-TW" });

  it("reads in the library's own words, and the cron itself on hover", () => {
    expect(periodText({ cron: "0 9 * * 1-5", tz: TPE, run: "w" }, at(TPE), t)).toEqual({
      text: "在 09:00, 星期一 到 星期五",
      set: "cron：0 9 * * 1-5（台北標準時間）",
    });
  });

  it("is not moved onto another clock: away from its zone it names the zone", () => {
    expect(periodText({ cron: "0 9 * * 1-5", run: "w" }, at(TPE), t).text).toBe(
      "在 09:00, 星期一 到 星期五（世界標準時間）",
    );
  });

  it("speaks the reader's language", () => {
    const english = { viewer: TPE, now: NOW, locale: "en" };
    expect(periodText({ cron: "0 9 * * 1-5", tz: TPE, run: "w" }, english, en).text).toBe(
      "At 09:00, Monday through Friday",
    );
  });

  it("a cron the library cannot read is shown as written", () => {
    expect(periodText({ cron: "0 9 * *", tz: TPE, run: "w" }, at(TPE), t).text).toBe("0 9 * *");
  });
});

describe("the clock arithmetic", () => {
  it("a wall time in a zone is one instant, and reads back as itself", () => {
    const ms = zonedMs(2026, 10, 8, 9, 0, TPE);
    expect(ms).toBe(Date.UTC(2026, 9, 8, 1, 0));
    expect(wallOf(ms, TPE)).toMatchObject({ y: 2026, mo: 10, d: 8, dow: 3, hh: 9, mm: 0 });
  });

  // The sweep's answer for each — Python's `datetime(..., tzinfo=ZoneInfo(z))`,
  // fold=0, the rule `next_run_ms` places a row's next run by: a repeated wall
  // time is its first reading, a skipped one is read with the offset before
  // the jump (so it lands after it).
  it.each([
    ["America/New_York", [2026, 11, 1, 1, 30], Date.UTC(2026, 10, 1, 5, 30)],
    ["Europe/London", [2026, 10, 25, 1, 30], Date.UTC(2026, 9, 25, 0, 30)],
    ["Australia/Sydney", [2026, 4, 5, 2, 30], Date.UTC(2026, 3, 4, 15, 30)],
    ["America/New_York", [2026, 3, 8, 2, 30], Date.UTC(2026, 2, 8, 7, 30)],
  ] as const)("a DST edge in %s %j is the instant the sweep uses", (zone, [y, mo, d, hh, mm], ms) => {
    expect(zonedMs(y, mo, d, hh, mm, zone)).toBe(ms);
  });

  it("moveTime carries a weekly across the date line onto the next day", () => {
    const moved = moveTime(
      { every: "weekly", dow: "sun", at: "20:00", dom: 0, tz: "America/New_York" },
      TPE,
      NOW,
    );
    expect(moved).toEqual({ at: "08:00", dow: "mon", dom: 0 });
  });

  it("zones are named in words, never by id when the browser has a name", () => {
    expect(zoneName("UTC", "zh-TW")).toBe("世界標準時間");
    expect(zoneName(TPE, "zh-TW")).toBe("台北標準時間");
    expect(zoneName("America/New_York", "en")).toBe("Eastern Time");
  });
});
