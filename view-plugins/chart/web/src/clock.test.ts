/**
 * #847/#848 P14: a time shows in its column's zone. A zoned column (the wire
 * names its zone) shows the wall time there, and names the zone; a zone-less
 * one shows as written, which the sandbox reads as UTC. Neither depends on the
 * VIEWER's zone: every case runs under two process zones and must agree.
 */
import { afterAll, describe, expect, it } from "vitest";

import { clockFor } from "./clock";

const ORIGINAL_TZ = process.env.TZ;
afterAll(() => {
  process.env.TZ = ORIGINAL_TZ;
});

const at = (iso: string) => Date.parse(iso);
const VIEWERS = ["UTC", "Asia/Tokyo", "America/Los_Angeles"];

function everywhere<T>(read: () => T): T {
  const seen = VIEWERS.map((tz) => {
    process.env.TZ = tz;
    return read();
  });
  for (const s of seen) expect(s).toEqual(seen[0]);
  return seen[0];
}

describe("clockFor", () => {
  it("(control) the viewer zones really differ in this runner", () => {
    const hours = VIEWERS.map((tz) => {
      process.env.TZ = tz;
      return new Date(0).getHours();
    });
    expect(hours).toEqual([0, 9, 16]);
  });

  it("shows a zoned time as the wall time there, and names the zone", () => {
    const taipei = clockFor("Asia/Taipei");
    expect(taipei.zone).toBe("Asia/Taipei");
    // Taipei midnight is 16:00 UTC the day before
    expect(everywhere(() => taipei.text(at("2026-02-28T16:00:00Z")))).toBe("2026-03-01");
    expect(everywhere(() => taipei.text(at("2026-03-01T04:30:00Z")))).toBe("2026-03-01 12:30");
    expect(everywhere(() => taipei.text(at("2026-03-01T04:30:05.250Z")))).toBe("2026-03-01 12:30:05.250");
  });

  it("follows a zone's own clock change", () => {
    const ny = clockFor("America/New_York");
    // 2026-03-08: New York springs from -05:00 to -04:00 at 02:00 local (07:00 UTC)
    expect(everywhere(() => ny.text(at("2026-03-08T06:59:00Z")))).toBe("2026-03-08 01:59");
    expect(everywhere(() => ny.text(at("2026-03-08T07:00:00Z")))).toBe("2026-03-08 03:00");
    expect(everywhere(() => ny.offset(at("2026-03-08T06:59:00Z")))).toBe(-5 * 3_600_000);
    expect(everywhere(() => ny.offset(at("2026-03-08T07:00:00Z")))).toBe(-4 * 3_600_000);
  });

  it("reads a fixed offset itself", () => {
    expect(everywhere(() => clockFor("+05:45").text(at("2026-03-01T00:00:00Z")))).toBe("2026-03-01 05:45");
    expect(everywhere(() => clockFor("-05:30").text(at("2026-03-01T00:00:00Z")))).toBe("2026-02-28 18:30");
    expect(clockFor("-05:30").zone).toBe("-05:30");
  });

  it("shows a zone-less time as written, naming no zone", () => {
    const plain = clockFor(undefined);
    expect(plain.zone).toBeNull();
    expect(everywhere(() => plain.text(at("2026-03-01T00:00:00Z")))).toBe("2026-03-01");
    expect(everywhere(() => plain.offset(at("2026-03-01T00:00:00Z")))).toBe(0);
  });

  it("places a wall time on a UTC axis: the instant plus its offset", () => {
    const taipei = clockFor("Asia/Taipei");
    expect(everywhere(() => taipei.wall(at("2026-02-28T16:00:00Z")))).toBe(at("2026-03-01T00:00:00Z"));
  });

  it("shows a zone the browser does not know as UTC, and says so", () => {
    const odd = clockFor("Mars/Olympus_Mons");
    expect(odd.zone).toBe("UTC");
    expect(odd.text(at("2026-03-01T00:00:00Z"))).toBe("2026-03-01");
  });
});
