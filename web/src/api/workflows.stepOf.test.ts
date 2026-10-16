import { describe, expect, it } from "vitest";

import { type PhaseNode, stepOf } from "./workflows";

const node = (id: string, status: string, current = false): PhaseNode => ({
  id,
  title: id,
  status,
  done: 0,
  total: 0,
  failed: 0,
  current,
});

// plan-workflow-out-of-chat D7, after NN/g "Progress Indicators": a long
// operation says how far along it is ("Step 3 of 7"), not just "Running…".
describe("stepOf", () => {
  it("counts the step being worked on, out of every declared step", () => {
    const nodes = [node("a", "passed"), node("b", "running", true), node("c", "pending")];
    expect(stepOf(nodes)).toEqual({ at: 2, of: 3 });
  });

  it("counts a step waiting for a person as the one it is on", () => {
    const nodes = [node("a", "passed"), node("b", "awaiting_human")];
    expect(stepOf(nodes)).toEqual({ at: 2, of: 2 });
  });

  it("reads all of them once every step has passed", () => {
    expect(stepOf([node("a", "passed"), node("b", "passed")])).toEqual({ at: 2, of: 2 });
  });

  it("reads none before the first step starts", () => {
    expect(stepOf([node("a", "pending"), node("b", "pending")])).toEqual({ at: 0, of: 2 });
  });
});
