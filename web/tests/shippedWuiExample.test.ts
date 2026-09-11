/**
 * The worked example's reducer, DRIVEN.
 *
 * `sample-skills/wui/examples/complete/` is what the skill tells an author to
 * read first, and it is copied into pages. Until now the only checks on it were
 * source-text: they asserted that `"step_failed"`, `"run_cancelled"` and
 * `e.reason` appear in the function body. A reducer that matches both types and
 * renders nothing satisfies all of that — and "Finished." is exactly what the
 * page then shows for a run that failed.
 *
 * So this drives it with the shapes the platform actually emits.
 *
 * OUTSIDE `src/` on purpose. The web image builds from `COPY web/ ./` alone
 * (docker/Dockerfile), so `sample-skills/` is not there — and `tsconfig`
 * includes `src`, so a file in `src` reaching across that boundary fails
 * `tsc --noEmit` inside the image with "Cannot find module". Vitest scans the
 * whole package by default and runs where the repo is whole, so the test still
 * drives the SHIPPED file rather than a copy. `web/tests/importBoundary.test.ts`
 * keeps `src` on its side of the line.
 */
import { describe, expect, it } from "vitest";

import { reduceRunEvent } from "../../sample-skills/wui/examples/complete/src/workspace";

const FRESH = { note: "", done: false, failed: false };

describe("the worked example's run reducer", () => {
  it("shows a failed step as failed, and says which gate gave up", () => {
    // `StepFailed` carries phase/name/reason/key — no `text`, no `message`.
    const out = reduceRunEvent(FRESH, {
      type: "step_failed",
      phase: "review",
      name: "check the numbers",
      reason: "the gate never passed",
      key: "",
    });

    expect(out.failed).toBe(true);
    expect(out.done).toBe(true);
    expect(out.note).toContain("the gate never passed");
  });

  it("shows a cancelled run as cancelled", () => {
    // `RunCancelled` carries nothing but its type.
    const out = reduceRunEvent(FRESH, { type: "run_cancelled" });

    expect(out.failed).toBe(true);
    expect(out.done).toBe(true);
    expect(out.note).not.toBe("");
  });

  it("still relays the platform's own sentence for a run-level error", () => {
    const out = reduceRunEvent(FRESH, { type: "error", message: "no capacity" });
    expect(out).toEqual({ note: "no capacity", done: true, failed: true });
  });

  it("does not call a run finished on a per-turn done", () => {
    // The control. `done` fires at the end of every TURN, so a workflow with
    // three agent steps sends three; treating the first as the finish
    // re-enables the page's button while the run is still going.
    const out = reduceRunEvent({ ...FRESH, note: "step one" }, { type: "done" });
    expect(out.done).toBe(false);
  });

  it("ignores an event type it has never heard of", () => {
    const prev = { note: "step one", done: false, failed: false };
    expect(reduceRunEvent(prev, { type: "something_new_next_year" })).toBe(prev);
  });
});
