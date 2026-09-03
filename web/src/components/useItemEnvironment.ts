/**
 * The two decisions the environment panel needs before it can render anything:
 * whether it belongs on this item at all, and whether this person has a budget
 * for the second half to be about.
 *
 * Pure functions rather than hook internals so each is testable on its own —
 * both of them are the kind of predicate that fails silently when wrong (a
 * panel that never appears, or one that appears showing `0 / 0`).
 */

import type { EnvBudget } from "./ItemEnvironmentPanel";

type ResourcesPayload = {
  limits: { count: number; cpu: number; memory_bytes: number; disk_bytes: number };
  cpu_in_use: number;
  memory_in_use: number;
};

/** The person's budget, or `null` when this deploy caps nobody.
 *
 * Only the FLOW dimensions count. Disk is a stock: it outlives every sandbox
 * and is freed by deleting files, never by closing anything — so a deploy that
 * caps only disk has nothing for this panel to say, and offering a cpu dial
 * there would imply the opposite.
 *
 * `null` rather than a zeroed budget, because `0 / 0` is not a reading and a
 * dial whose ceiling is "unlimited" is worse than no dial. This is the shipped
 * default, so it is the state every deployment is in until someone configures
 * one — which makes it the case to get right, not the edge case. */
export function budgetFrom(resources: ResourcesPayload | undefined): EnvBudget | null {
  if (!resources) return null;
  const { limits } = resources;
  if (!limits.cpu && !limits.memory_bytes) return null;
  return {
    cpu: limits.cpu,
    memoryBytes: limits.memory_bytes,
    cpuInUse: resources.cpu_in_use,
    memoryInUse: resources.memory_in_use,
  };
}
