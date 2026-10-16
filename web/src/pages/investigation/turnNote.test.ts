// docs/plan-interjection.md Q2/Q7: what the composer says while a turn runs.
import { describe, expect, it } from "vitest";

import { EMPTY_LOG, type AgentLog } from "./agentLog";
import { turnNote } from "./turnNote";

const running = (by: string | null): AgentLog => ({ ...EMPTY_LOG, streaming: true, streamingBy: by });

describe("turnNote", () => {
  it("says nothing when no turn runs", () => {
    expect(turnNote(EMPTY_LOG, "ann")).toBeNull();
  });

  it("on your own running turn: what a message sent now can and cannot change", () => {
    expect(turnNote(running("ann"), "ann")).toEqual({ kind: "own" });
  });

  it("on someone else's running turn: sending takes it over", () => {
    expect(turnNote(running("bob"), "ann")).toEqual({ kind: "others", who: "bob" });
  });

  it("says nothing until it knows who you are, rather than calling your own turn someone else's", () => {
    expect(turnNote(running("ann"), undefined)).toBeNull();
    expect(turnNote(running("ann"), null)).toBeNull();
  });

  it("a turn nobody is named for (a goal round, a reload) is not claimed as anyone's", () => {
    expect(turnNote(running(null), "ann")).toBeNull();
  });
});
