// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentEvent } from "../events";
import {
  EMPTY_LOG,
  type AgentLog,
  type AgentMetricsState,
  reduceAgent,
} from "../pages/investigation/agentLog";
import { TurnStatus } from "./TurnStatus";

const up: AgentMetricsState = { phase: "up", promptTokens: 256, completionTokens: 0, elapsedMs: 0 };
const down: AgentMetricsState = { phase: "down", promptTokens: 256, completionTokens: 4, elapsedMs: 600 };

const streaming = (over: Partial<AgentLog> = {}): AgentLog => ({ ...EMPTY_LOG, streaming: true, ...over });
const fold = (events: AgentEvent[], from: AgentLog = EMPTY_LOG): AgentLog =>
  events.reduce((log, ev) => reduceAgent(log, ev), from);

describe("TurnStatus", () => {
  afterEach(cleanup);

  it("renders nothing when no turn is in flight", () => {
    const { container } = render(<TurnStatus log={EMPTY_LOG} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says 準備中 while the backend is still handing off (no metrics yet)", () => {
    render(<TurnStatus log={streaming()} />);
    expect(screen.getByText(/準備中/)).toBeInTheDocument();
  });

  it("says 等候模型回應 once the prompt is with the model but no token has streamed", () => {
    render(<TurnStatus log={streaming({ metrics: up })} />);
    expect(screen.getByText(/等候模型回應/)).toBeInTheDocument();
  });

  it("says 思考中 while the model streams reasoning", () => {
    const log = fold([{ type: "message_delta", text: "hmm", reasoning: true }]);
    render(<TurnStatus log={{ ...log, streaming: true, metrics: down }} />);
    expect(screen.getByText(/思考中/)).toBeInTheDocument();
  });

  it("shows the token metrics line once the answer is streaming", () => {
    const log = fold([{ type: "message_delta", text: "Here is the answer" }]);
    render(<TurnStatus log={{ ...log, streaming: true, metrics: down }} />);
    expect(screen.getByText(/tok\/s/)).toBeInTheDocument();
    expect(screen.queryByText(/等候模型回應/)).not.toBeInTheDocument();
  });

  it("defers to the running-tool line (not the waiting copy) during a tool call", () => {
    const log = fold([
      { type: "message_delta", text: "calling", reasoning: true },
      { type: "tool_start", call_id: "t1", name: "kb_search", args: {} },
    ]);
    render(<TurnStatus log={{ ...log, streaming: true, metrics: down }} />);
    expect(screen.queryByText(/等候模型回應/)).not.toBeInTheDocument();
    expect(screen.getByText(/running/)).toBeInTheDocument();
  });

  it("ticks a never-freeze elapsed timer while waiting", () => {
    vi.useFakeTimers();
    try {
      render(<TurnStatus log={streaming({ metrics: up })} />);
      act(() => void vi.advanceTimersByTime(12_000));
      expect(screen.getByText(/12s/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("escalates the waiting reassurance as the wait grows", () => {
    vi.useFakeTimers();
    try {
      render(<TurnStatus log={streaming({ metrics: up })} />);
      act(() => void vi.advanceTimersByTime(16_000));
      expect(screen.getByText(/模型忙碌中/)).toBeInTheDocument();
      act(() => void vi.advanceTimersByTime(30_000)); // 46s total
      expect(screen.getByText(/可隨時按 Stop/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("nudges when the backend prep itself drags on", () => {
    vi.useFakeTimers();
    try {
      render(<TurnStatus log={streaming()} />); // no metrics → prep
      act(() => void vi.advanceTimersByTime(5_000));
      expect(screen.getByText(/還在準備/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a de-jargoned switch notice while waiting after a failover (#249/#131)", () => {
    // waiting phase (metrics 'up', no token yet) + a failover this turn.
    render(<TurnStatus log={streaming({ metrics: up, failover: { at: 1 } })} />);
    expect(screen.getByText(/已自動切換/)).toBeInTheDocument();
    expect(screen.queryByText(/等候模型回應/)).not.toBeInTheDocument(); // the notice replaces it
  });

  it("does not show the switch notice once the model is answering", () => {
    const log = fold([{ type: "message_delta", text: "hi", reasoning: false }]);
    render(<TurnStatus log={{ ...log, streaming: true, metrics: down, failover: { at: 1 } }} />);
    expect(screen.queryByText(/已自動切換/)).not.toBeInTheDocument(); // a token arrived → gone
  });

  it("shows '還原工作區… N/M' while a cold sandbox restores, over the tool line (#492 P11)", () => {
    // The restore happens INSIDE the first tool's lazy wake, so a tool is
    // 'running' with metrics present — yet the restore line must take precedence.
    const log = fold([
      { type: "tool_start", call_id: "t1", name: "exec", args: {} },
      { type: "restore_progress", done: 3, total: 10 },
    ]);
    render(<TurnStatus log={{ ...log, streaming: true, metrics: down }} />);
    expect(screen.getByText(/還原工作區/)).toHaveTextContent("3/10");
    expect(screen.queryByText(/running/)).not.toBeInTheDocument(); // restore replaces it
  });

  it("reverts to the running-tool line once restore completes (#492 P11)", () => {
    const log = fold([
      { type: "tool_start", call_id: "t1", name: "exec", args: {} },
      { type: "restore_progress", done: 10, total: 10 },
      { type: "tool_log", text: "output", call_id: "t1" }, // clears restore
    ]);
    render(<TurnStatus log={{ ...log, streaming: true, metrics: down }} />);
    expect(screen.queryByText(/還原工作區/)).not.toBeInTheDocument();
    expect(screen.getByText(/running/)).toBeInTheDocument();
  });
});
