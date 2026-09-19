import { describe, expect, it } from "vitest";

import { isValidCpu, isValidMemory, normaliseMemory, toSizeString } from "./ItemEnvironmentSize";

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

  it("memory: empty is the default; otherwise a size a person would write, not zero", () => {
    expect(isValidMemory("")).toBe(true);
    expect(isValidMemory("512M")).toBe(true);
    expect(isValidMemory("512MB")).toBe(true);
    expect(isValidMemory("512 mb")).toBe(true);
    expect(isValidMemory("512.0 MB")).toBe(true); // the display format, typed back
    expect(isValidMemory("1.5G")).toBe(true);
    expect(isValidMemory("1000000")).toBe(true);
    expect(isValidMemory("0")).toBe(false);
    expect(isValidMemory("0M")).toBe(false);
    expect(isValidMemory("0.0 GB")).toBe(false);
    expect(isValidMemory("max")).toBe(false);
    expect(isValidMemory("abc")).toBe(false);
    expect(isValidMemory("512 MiB")).toBe(false);
    expect(isValidMemory("1.5")).toBe(false); // a fraction of a byte is nothing
  });
});

describe("normaliseMemory — what a person writes → what the server reads", () => {
  it("accepts M and MB alike (and K/G/T likewise), any case, with or without a space", () => {
    for (const text of ["512M", "512MB", "512 MB", "512mb", " 512 m ", "512.0 MB"]) {
      expect(normaliseMemory(text), text).toBe("512M");
    }
    expect(normaliseMemory("2GB")).toBe("2G");
    expect(normaliseMemory("3 kb")).toBe("3K");
    expect(normaliseMemory("1TB")).toBe("1T");
  });

  it("turns a fraction into the exact smaller unit the server can parse", () => {
    expect(normaliseMemory("1.5G")).toBe("1536M");
    expect(normaliseMemory("0.5 MB")).toBe("512K");
    expect(normaliseMemory("2.5K")).toBe("2560");
  });

  it("reads full-width digits as digits — the server's str.isdigit does too", () => {
    expect(normaliseMemory("５１２M")).toBe("512M");
    expect(normaliseMemory("１.５ GB")).toBe("1536M");
  });

  it("passes a bare byte count through, and refuses what it cannot read", () => {
    expect(normaliseMemory("1000000")).toBe("1000000");
    expect(normaliseMemory("")).toBeNull();
    expect(normaliseMemory("abc")).toBeNull();
    expect(normaliseMemory("512 MiB")).toBeNull();
    expect(normaliseMemory("0")).toBeNull();
  });
});
