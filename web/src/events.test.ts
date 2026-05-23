import { describe, expect, it } from "vitest";
import type { AgentEvent } from "./events";
import { isTerminal } from "./events";

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
