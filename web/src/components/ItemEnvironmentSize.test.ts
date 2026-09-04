/**
 * What a save actually sends.
 *
 * Adversarial review, finding 6: the modal hard-coded `memory: null` on every
 * save, and the route is a whole-value replace — so editing CPU, or clicking
 * "back to default", DESTROYED a stored memory setting. Silent data loss, with
 * no way to put it back because the panel had no memory control at all.
 *
 * The api client's own docstring said the opposite ("omitting one would read as
 * 'leave that dimension alone'"), which is exactly the kind of comment that
 * makes a bug survive review: it describes an intention the server never had.
 * PUT replaces, so the client has to send the whole state — and this is the
 * function that decides what "the whole state" is.
 */

import { describe, expect, it } from "vitest";

import { sizeToSave } from "./ItemEnvironmentSize";

const STATED = { statedCpuCores: 2, statedMemoryBytes: 512 * 1024 ** 2 };

describe("sizeToSave", () => {
  it("carries the other dimension through when only cpu changed", () => {
    const got = sizeToSave(STATED, { cpuCores: 1 });

    expect(got.cpuCores).toBe(1);
    expect(got.memory).toBe("512M");
  });

  it("carries cpu through when only memory changed", () => {
    const got = sizeToSave(STATED, { memory: "1G" });

    expect(got.cpuCores).toBe(2);
    expect(got.memory).toBe("1G");
  });

  it("clears only the dimension the person cleared", () => {
    // "Back to default" on cpu must not take memory with it — that was the
    // whole defect.
    const got = sizeToSave(STATED, { cpuCores: null });

    expect(got.cpuCores).toBeNull();
    expect(got.memory).toBe("512M");
  });

  it("keeps an unset dimension unset rather than inventing a number", () => {
    const got = sizeToSave({ statedCpuCores: null, statedMemoryBytes: null }, { cpuCores: 4 });

    expect(got.cpuCores).toBe(4);
    expect(got.memory).toBeNull();
  });

  it("renders bytes in the spelling the server parses", () => {
    // The wire takes the operator's spelling so one parser owns the vocabulary.
    // A raw byte count would be accepted too, but `2G` is what a person reading
    // the request sees, and round-tripping through the same words is what keeps
    // the panel and config.yaml describing the same thing.
    expect(sizeToSave({ statedCpuCores: null, statedMemoryBytes: 2 * 1024 ** 3 }, {}).memory).toBe(
      "2G",
    );
    expect(sizeToSave({ statedCpuCores: null, statedMemoryBytes: 1536 }, {}).memory).toBe("1536");
  });
});
