import { describe, expect, it } from "vitest";
import type { AgentEvent } from "./events";
import { isTerminal, isTurnProgress } from "./events";

describe("isTerminal", () => {
  const cases: { event: AgentEvent; terminal: boolean }[] = [
    { event: { type: "message_delta", text: "x" }, terminal: false },
    {
      event: { type: "tool_start", call_id: "c", name: "n", args: {} },
      terminal: false,
    },
    { event: { type: "tool_end", call_id: "c", output: "" }, terminal: false },
    { event: { type: "sandbox_killed_idle" }, terminal: false },
    {
      event: {
        type: "tool_call_parse_error",
        call_id: "c",
        raw: "",
        hint: "",
      },
      terminal: false,
    },
    { event: { type: "done" }, terminal: true },
    { event: { type: "error", message: "x" }, terminal: true },
    { event: { type: "run_cancelled" }, terminal: true },
    { event: { type: "max_turns_exceeded", turns: 5 }, terminal: true },
  ];

  for (const { event, terminal } of cases) {
    it(`${event.type} → ${terminal}`, () => {
      expect(isTerminal(event)).toBe(terminal);
    });
  }
});

describe("isTurnProgress — a re-sent model call is the turn moving (docs/plan-friendly-errors.md)", () => {
  it("counts a retry and a withdrawal as progress, like a rate-limit hold", () => {
    expect(isTurnProgress({ type: "rate_limited", seconds: 5 })).toBe(true);
    expect(isTurnProgress({ type: "model_retrying", attempt: 1, kind: "timeout" })).toBe(true);
    expect(isTurnProgress({ type: "output_retracted", text_chars: 3, reasoning_chars: 0 })).toBe(true);
  });
});
