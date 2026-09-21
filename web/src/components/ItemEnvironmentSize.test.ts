import { describe, expect, it } from "vitest";

import { cpuFault, memoryFault, normaliseMemory, parseSize, toSizeString } from "./ItemEnvironmentSize";

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
  // The ceilings come from the RECORD (#830), so every case names one.
  const MAX_CORES = 1024;

  it("cpu: empty is the default; otherwise a finite number above 0, no larger than the ceiling", () => {
    expect(cpuFault("", MAX_CORES)).toBeNull();
    expect(cpuFault("0.5", MAX_CORES)).toBeNull();
    expect(cpuFault("2", MAX_CORES)).toBeNull();
    expect(cpuFault("1024", MAX_CORES)).toBeNull(); // the bound itself passes (`<=`)
    for (const text of ["0", "-1", "abc", "Infinity"]) {
      expect(cpuFault(text, MAX_CORES)?.type, text).toBe("unreadable");
    }
    for (const text of ["1024.5", "2048"]) {
      expect(cpuFault(text, MAX_CORES)?.type, text).toBe("over");
    }
  });

  it("cpu: `over` carries the RECORD's ceiling, `unreadable` the grammar — data, not copy", () => {
    // A ceiling the server never used, so a detail that agrees with it can
    // only have come from the argument — not from a number of this module's own.
    expect(cpuFault("8", 7)).toEqual({ type: "over", detail: "7" });
    expect(cpuFault("7", 7)).toBeNull();
    // The examples the grammar hint shows, joined for either locale.
    expect(cpuFault("0", 7)).toEqual({ type: "unreadable", detail: "1 / 0.5" });
  });

  const MAX_BYTES = 1024 ** 5;

  it("memory: empty is the default; otherwise a size a person would write, not zero, within the ceiling", () => {
    expect(memoryFault("", MAX_BYTES)).toBeNull();
    expect(memoryFault("512M", MAX_BYTES)).toBeNull();
    expect(memoryFault("512MB", MAX_BYTES)).toBeNull();
    expect(memoryFault("512 mb", MAX_BYTES)).toBeNull();
    expect(memoryFault("512.0 MB", MAX_BYTES)).toBeNull(); // the display format, typed back
    expect(memoryFault("1.5G", MAX_BYTES)).toBeNull();
    expect(memoryFault("1000000", MAX_BYTES)).toBeNull();
    expect(memoryFault("1024T", MAX_BYTES)).toBeNull(); // the bound itself passes (`<=`)
    // `2P`: P is not a unit the server reads, so it is unreadable, not over.
    for (const text of ["0", "0M", "0.0 GB", "max", "abc", "512 MiB", "1.5", "2P"]) {
      expect(memoryFault(text, MAX_BYTES)?.type, text).toBe("unreadable");
    }
    for (const text of ["1025T", "1024.5 TB", "1048577G"]) {
      expect(memoryFault(text, MAX_BYTES)?.type, text).toBe("over");
    }
  });

  it("memory: `over` carries the RECORD's ceiling in the server's spelling, `unreadable` the grammar", () => {
    // `3G`, the spelling a person can type back — not `3.0 GB`.
    expect(memoryFault("3073M", 3 * 1024 ** 3)).toEqual({ type: "over", detail: "3G" });
    expect(memoryFault("3G", 3 * 1024 ** 3)).toBeNull();
    expect(memoryFault("abc", 3 * 1024 ** 3)).toEqual({
      type: "unreadable",
      detail: "512M / 512MB / 1.5G",
    });
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

describe("parseSize — the server's spelling back to bytes", () => {
  it("round-trips what normaliseMemory produces", () => {
    for (const text of ["1.5 GB", "512MB", "2.5K", "1000000", "1TB"]) {
      const wire = normaliseMemory(text)!;
      expect(parseSize(wire), text).toBe(parseSize(normaliseMemory(toSizeString(parseSize(wire)!)!)));
    }
    expect(parseSize("1536M")).toBe(1536 * 1024 ** 2);
    expect(parseSize("2G")).toBe(2 * 1024 ** 3);
    expect(parseSize("1000000")).toBe(1000000);
    expect(parseSize(null)).toBeNull();
    expect(parseSize("x")).toBeNull();
  });
});

