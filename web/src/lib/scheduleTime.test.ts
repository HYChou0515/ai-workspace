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

describe("the clock arithmetic", () => {
  it("a wall time in a zone is one instant, and reads back as itself", () => {
    const ms = zonedMs(2026, 10, 8, 9, 0, TPE);
    expect(ms).toBe(Date.UTC(2026, 9, 8, 1, 0));
    expect(wallOf(ms, TPE)).toMatchObject({ y: 2026, mo: 10, d: 8, dow: 3, hh: 9, mm: 0 });
  });

  it("a wall time a fall-back repeats is its first reading", () => {
    expect(zonedMs(2026, 11, 1, 1, 30, "America/New_York")).toBe(Date.UTC(2026, 10, 1, 5, 30));
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
