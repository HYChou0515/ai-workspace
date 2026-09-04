/**
 * What the panel is allowed to ask for, and when it should not exist at all.
 *
 * Pure decisions, tested without a DOM: whether to mount, whether this viewer
 * may spend the owner's quota, and how the person's budget is derived from the
 * `/me/resources` payload the page already has.
 */

import { describe, expect, it } from "vitest";

import { budgetFrom } from "./useItemEnvironment";


describe("budgetFrom", () => {
  it("is null when this deploy caps nobody", () => {
    // The shipped default: every dimension 0. `0 / 0` is not a reading, and a
    // dial whose ceiling is unlimited is worse than no dial.
    const got = budgetFrom({
      limits: { count: 0, cpu: 0, memory_bytes: 0, disk_bytes: 0 },
      cpu_in_use: 0,
      memory_in_use: 0,
    });

    expect(got).toBeNull();
  });

  it("is a budget as soon as ANY flow dimension is capped", () => {
    const got = budgetFrom({
      limits: { count: 0, cpu: 4, memory_bytes: 0, disk_bytes: 0 },
      cpu_in_use: 2,
      memory_in_use: 0,
    });

    expect(got).toEqual({ cpu: 4, memoryBytes: 0, cpuInUse: 2, memoryInUse: 0 });
  });

  it("ignores a disk-only cap", () => {
    // Disk is a STOCK — it survives the sandbox and is freed by deleting files,
    // not by closing anything. A deploy that caps only disk has nothing for this
    // panel to say, and showing a cpu dial there would imply otherwise.
    const got = budgetFrom({
      limits: { count: 0, cpu: 0, memory_bytes: 0, disk_bytes: 1024 },
      cpu_in_use: 0,
      memory_in_use: 0,
    });

    expect(got).toBeNull();
  });

  it("is null while the payload is still loading", () => {
    expect(budgetFrom(undefined)).toBeNull();
  });
});
