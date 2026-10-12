import { describe, expect, it } from "vitest";

import { executionPath, isExecutionPath, runIdOf } from "./executionTab";

// plan-workflow-out-of-chat D7: an execution opens as an editor tab. A tab's
// identity is its path, so an execution gets a path no file can have.
describe("execution tab path", () => {
  it("names an execution by its run, and reads the run back", () => {
    const path = executionPath("run:abc-123");
    expect(isExecutionPath(path)).toBe(true);
    expect(runIdOf(path)).toBe("run:abc-123");
  });

  it("is never a workspace file's path", () => {
    // workspace paths are absolute (`/notes.md`) or relative (`notes.md`); a
    // file named like the prefix is still a file
    for (const file of ["/notes.md", "notes.md", "/workflow-run/x", "workflow-run.md"]) {
      expect(isExecutionPath(file)).toBe(false);
      expect(runIdOf(file)).toBeNull();
    }
  });
});
