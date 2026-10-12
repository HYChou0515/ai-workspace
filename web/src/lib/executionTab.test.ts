import { describe, expect, it } from "vitest";

import {
  executionName,
  executionPath,
  isExecutionPath,
  runIdOf,
  statusText,
} from "./executionTab";

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

// D10: an execution is named by its workflow and when it started.
describe("executionName", () => {
  it("is the workflow's title and its start, month/day hour:minute", () => {
    const at = new Date(2026, 9, 12, 14, 3).getTime(); // local time, as shown
    expect(executionName("Classify wafers", at)).toBe("Classify wafers · 10/12 14:03");
  });

  it("is the title alone before it has started", () => {
    expect(executionName("Classify wafers", null)).toBe("Classify wafers");
  });
});

describe("statusText", () => {
  it("says each status in the words a person uses", () => {
    expect(
      (["pending", "running", "awaiting_human", "done", "error", "cancelled"] as const).map(
        statusText,
      ),
    ).toEqual(["Starting", "Running", "Waiting for you", "Done", "Failed", "Stopped"]);
  });
});
