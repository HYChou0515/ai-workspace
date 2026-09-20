import { describe, expect, it } from "vitest";

import { cpuFault, memoryBytes, memoryFault, normaliseMemory, toSizeString } from "./ItemEnvironmentSize";

describe("toSizeString — bytes the way the server reads them", () => {
  it("uses the largest unit that divides exactly, else the bare byte count", () => {
    expect(toSizeString(512 * 1024 ** 2)).toBe("512M");
    expect(toSizeString(2 * 1024 ** 3)).toBe("2G");
    expect(toSizeString(1536 * 1024 ** 2)).toBe("1536M"); // 1.5 GiB is not a whole G
    expect(toSizeString(3 * 1024)).toBe("3K");
    expect(toSizeString(1000000)).toBe("1000000");
    expect(toSizeString(1024 ** 5)).toBe("1024T"); // the server's own ceiling, in its own spelling
    expect(toSizeString(null)).toBeNull();
  });
});

describe("the server's refusals, asked first", () => {
  // The ceilings come from the RECORD (#830), so every case names one.
  const MAX_CORES = 1024;
  const MAX_BYTES = 1024 ** 5;

  it("cpu: empty is the default; otherwise a finite number above 0, no larger than the ceiling", () => {
    expect(cpuFault("", MAX_CORES)).toBeNull();
    expect(cpuFault("0.5", MAX_CORES)).toBeNull();
    expect(cpuFault("2", MAX_CORES)).toBeNull();
    expect(cpuFault("1024", MAX_CORES)).toBeNull(); // the bound itself passes (`<=`)
    expect(cpuFault("0", MAX_CORES)).toBe("unreadable");
    expect(cpuFault("-1", MAX_CORES)).toBe("unreadable");
    expect(cpuFault("abc", MAX_CORES)).toBe("unreadable");
    expect(cpuFault("Infinity", MAX_CORES)).toBe("unreadable");
    expect(cpuFault("1024.5", MAX_CORES)).toBe("over");
    expect(cpuFault("2048", MAX_CORES)).toBe("over");
  });

  it("cpu: the ceiling is whatever the record says, not a number of this module's own", () => {
    expect(cpuFault("8", 7)).toBe("over");
    expect(cpuFault("7", 7)).toBeNull();
    // No ceiling known (a record without one): nothing is refused as over.
    expect(cpuFault("1e300", Number.POSITIVE_INFINITY)).toBeNull();
  });

  it("memory: empty is the default; otherwise a size a person would write, not zero, within the ceiling", () => {
    expect(memoryFault("", MAX_BYTES)).toBeNull();
    expect(memoryFault("512M", MAX_BYTES)).toBeNull();
    expect(memoryFault("512MB", MAX_BYTES)).toBeNull();
    expect(memoryFault("512 mb", MAX_BYTES)).toBeNull();
    expect(memoryFault("512.0 MB", MAX_BYTES)).toBeNull(); // the display format, typed back
    expect(memoryFault("1.5G", MAX_BYTES)).toBeNull();
    expect(memoryFault("1000000", MAX_BYTES)).toBeNull();
    expect(memoryFault("1024T", MAX_BYTES)).toBeNull(); // the bound itself passes (`<=`)
    expect(memoryFault("0", MAX_BYTES)).toBe("unreadable");
    expect(memoryFault("0M", MAX_BYTES)).toBe("unreadable");
    expect(memoryFault("0.0 GB", MAX_BYTES)).toBe("unreadable");
    expect(memoryFault("max", MAX_BYTES)).toBe("unreadable");
    expect(memoryFault("abc", MAX_BYTES)).toBe("unreadable");
    expect(memoryFault("512 MiB", MAX_BYTES)).toBe("unreadable");
    expect(memoryFault("1.5", MAX_BYTES)).toBe("unreadable"); // a fraction of a byte is nothing
    expect(memoryFault("2P", MAX_BYTES)).toBe("unreadable"); // P is not a unit the server reads
    expect(memoryFault("1025T", MAX_BYTES)).toBe("over");
    expect(memoryFault("1024.5 TB", MAX_BYTES)).toBe("over"); // a fraction over is still over
    expect(memoryFault("1048577G", MAX_BYTES)).toBe("over");
  });

  it("memory: the ceiling is whatever the record says", () => {
    expect(memoryFault("3073M", 3 * 1024 ** 3)).toBe("over");
    expect(memoryFault("3G", 3 * 1024 ** 3)).toBeNull();
    expect(memoryFault("9".repeat(40), Number.POSITIVE_INFINITY)).toBeNull();
  });
});

describe("memoryBytes — what a person writes, as the number the server will store", () => {
  it("reads M and MB alike (and K/G/T likewise), any case, with or without a space", () => {
    for (const text of ["512M", "512MB", "512 MB", "512mb", " 512 m ", "512.0 MB"]) {
      expect(memoryBytes(text), text).toBe(512 * 1024 ** 2);
    }
    expect(memoryBytes("2GB")).toBe(2 * 1024 ** 3);
    expect(memoryBytes("3 kb")).toBe(3 * 1024);
    expect(memoryBytes("1TB")).toBe(1024 ** 4);
  });

  it("turns a fraction into whole bytes", () => {
    expect(memoryBytes("1.5G")).toBe(1536 * 1024 ** 2);
    expect(memoryBytes("0.5 MB")).toBe(512 * 1024);
    expect(memoryBytes("2.5K")).toBe(2560);
  });

  it("reads full-width digits as digits — the server's str.isdigit does too", () => {
    expect(memoryBytes("５１２M")).toBe(512 * 1024 ** 2);
    expect(memoryBytes("１.５ GB")).toBe(1536 * 1024 ** 2);
  });

  it("passes a bare byte count through, and refuses what it cannot read", () => {
    expect(memoryBytes("1000000")).toBe(1000000);
    expect(memoryBytes("")).toBeNull();
    expect(memoryBytes("abc")).toBeNull();
    expect(memoryBytes("512 MiB")).toBeNull();
    expect(memoryBytes("0")).toBeNull();
    expect(memoryBytes("1.5")).toBeNull();
  });
});

describe("normaliseMemory — what a person writes → what the server reads", () => {
  it("is memoryBytes in the server's spelling", () => {
    for (const text of ["512M", "512MB", "512 MB", "512mb", " 512 m ", "512.0 MB"]) {
      expect(normaliseMemory(text), text).toBe("512M");
    }
    expect(normaliseMemory("2GB")).toBe("2G");
    expect(normaliseMemory("3 kb")).toBe("3K");
    expect(normaliseMemory("1TB")).toBe("1T");
    expect(normaliseMemory("1.5G")).toBe("1536M");
    expect(normaliseMemory("0.5 MB")).toBe("512K");
    expect(normaliseMemory("2.5K")).toBe("2560");
    expect(normaliseMemory("５１２M")).toBe("512M");
    expect(normaliseMemory("1000000")).toBe("1000000");
    expect(normaliseMemory("")).toBeNull();
    expect(normaliseMemory("abc")).toBeNull();
    expect(normaliseMemory("0")).toBeNull();
  });
});
