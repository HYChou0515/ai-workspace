import { describe, expect, it } from "vitest";

import { isValidCpu, isValidMemory, toSizeString } from "./ItemEnvironmentSize";

describe("toSizeString — bytes the way the server reads them", () => {
  it("uses the largest unit that divides exactly, else the bare byte count", () => {
    expect(toSizeString(512 * 1024 ** 2)).toBe("512M");
    expect(toSizeString(2 * 1024 ** 3)).toBe("2G");
    expect(toSizeString(1536 * 1024 ** 2)).toBe("1536M"); // 1.5 GiB is not a whole G
    expect(toSizeString(3 * 1024)).toBe("3K");
    expect(toSizeString(1000000)).toBe("1000000");
    expect(toSizeString(null)).toBeNull();
  });
});

describe("the server's refusals, asked first", () => {
  it("cpu: empty is the default; otherwise a finite number above 0", () => {
    expect(isValidCpu("")).toBe(true);
    expect(isValidCpu("0.5")).toBe(true);
    expect(isValidCpu("2")).toBe(true);
    expect(isValidCpu("0")).toBe(false);
    expect(isValidCpu("-1")).toBe(false);
    expect(isValidCpu("abc")).toBe(false);
    expect(isValidCpu("Infinity")).toBe(false);
  });

  it("memory: empty is the default; otherwise digits with an optional K/M/G/T, not zero", () => {
    expect(isValidMemory("")).toBe(true);
    expect(isValidMemory("512M")).toBe(true);
    expect(isValidMemory("512m")).toBe(true);
    expect(isValidMemory(" 2G ")).toBe(true);
    expect(isValidMemory("1000000")).toBe(true);
    // What the display format looks like — and what the server refuses.
    expect(isValidMemory("512.0 MB")).toBe(false);
    expect(isValidMemory("512 MB")).toBe(false);
    expect(isValidMemory("1.5G")).toBe(false);
    expect(isValidMemory("0")).toBe(false);
    expect(isValidMemory("0M")).toBe(false);
    expect(isValidMemory("max")).toBe(false);
  });
});
