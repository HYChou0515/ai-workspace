// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
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
const down: AgentMetricsState = {
  phase: "down",
  promptTokens: 256,
  completionTokens: 4,
  elapsedMs: 600,
  // #748: a turn that was actually timed. Without this the rate is null
  // by design — an untimed turn has no speed to report.
  generationMs: 600,
};

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

  it("says the turn is waiting out a rate limit, and for how long", () => {
    // Waiting is the only cure for a 429, so the turn deliberately goes quiet
    // for as long as the provider asked. Without a line saying so, the fix
    // reads as a hang — the user cannot tell it apart from a dead turn.
    const log = fold([{ type: "rate_limited", seconds: 30 }]);
    render(<TurnStatus log={{ ...log, streaming: true }} />);
    expect(screen.getByText(/請求過於頻繁/)).toHaveTextContent("30");
  });

  it("keeps the retry affordance and a moving clock while thinking (#748 review)", () => {
    // The thinking branch returned early, so it dropped both the retry button
    // and this component's own FE-anchored clock — and then printed the
    // BACKEND's elapsed, which is exactly the number the component's docstring
    // says stalls when the server wedges. A model that thinks for minutes and
    // then hangs showed a frozen "· 4.0s" and offered no way out.
    const log = fold([
      { type: "message_delta", text: "thinking", reasoning: true },
      { type: "agent_metrics", phase: "down", prompt_tokens: 8412, completion_tokens: 120, elapsed_ms: 4000, generation_ms: 4000 },
    ]);
    // `elapsedSec` comes from a ref the component sets on its first streaming
    // render, so the 60s threshold is only reachable by moving the clock.
    const real = Date.now;
    const t0 = real();
    const view = render(
      <TurnStatus log={{ ...log, streaming: true }} onRetry={() => {}} />,
    );
    Date.now = () => t0 + 90_000;
    try {
      view.rerender(<TurnStatus log={{ ...log, streaming: true }} onRetry={() => {}} />);
      expect(screen.getByTestId("turn-retry")).toBeInTheDocument();
      // and the counts are on that same line, not in a branch of their own
      expect(screen.getByText(/↓ 120 tok/)).toBeInTheDocument();
    } finally {
      Date.now = real;
    }
  });

  it("does not relabel a running tool as thinking (#748 review)", () => {
    // A reasoning model that thinks, emits no prose, then calls a tool is BOTH
    // `thinking` and `toolRunning`. The new branch sat above the tool branch and
    // called formatMetrics without the flag, so the ⏳ running… signal vanished
    // for exactly the turns the phase was added to serve.
    const log = fold([
      { type: "message_delta", text: "thinking", reasoning: true },
      { type: "agent_metrics", phase: "down", prompt_tokens: 8412, completion_tokens: 120, elapsed_ms: 4000, generation_ms: 4000 },
      { type: "tool_start", call_id: "t1", name: "kb_search", args: {} },
    ]);
    render(<TurnStatus log={{ ...log, streaming: true }} />);
    expect(screen.getByText(/running/)).toBeInTheDocument();
  });

  it("keeps showing the numbers while the model is only thinking (#748)", () => {
    // A reasoning model can think for a long time before any visible content.
    // The backend pushes metrics throughout — `completion_chars` counts the
    // reasoning deltas too — but the line was rendered only for `answering`,
    // so the longest silence of the turn was also the emptiest. Nothing was
    // broken; the numbers simply had nowhere to go.
    const log = fold([
      { type: "message_delta", text: "let me think", reasoning: true },
      {
        type: "agent_metrics",
        phase: "down",
        prompt_tokens: 8412,
        completion_tokens: 120,
        elapsed_ms: 4000,
        generation_ms: 4000,
      },
    ]);
    render(<TurnStatus log={{ ...log, streaming: true }} />);
    expect(screen.getByText(/↓ 120 tok/)).toBeInTheDocument();
    expect(screen.getByText(/30 tok\/s/)).toBeInTheDocument();
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

/**
 * After 40 seconds the copy stopped changing and the counter just climbed — past
 * a minute, past an hour. The only ways out were Stop (which abandons the turn)
 * and starting a new chat (which abandons the thread). A wait that cannot be
 * acted on is the state the user reads as "it's broken", so a long one has to
 * offer the obvious action: ask again.
 */
describe("TurnStatus — a way out of a long wait", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("offers no retry while the wait is still ordinary", () => {
    render(<TurnStatus log={streaming()} onRetry={vi.fn()} />);
    expect(screen.queryByTestId("turn-retry")).not.toBeInTheDocument();
  });

  it("offers a retry once the wait has gone on too long", async () => {
    vi.useFakeTimers();
    render(<TurnStatus log={streaming()} onRetry={vi.fn()} />);
    await act(async () => {
      vi.advanceTimersByTime(90_000);
    });
    expect(screen.getByTestId("turn-retry")).toBeInTheDocument();
  });

  it("asks again when the retry is taken", async () => {
    vi.useFakeTimers();
    const onRetry = vi.fn();
    render(<TurnStatus log={streaming()} onRetry={onRetry} />);
    await act(async () => {
      vi.advanceTimersByTime(90_000);
    });
    fireEvent.click(screen.getByTestId("turn-retry"));
    expect(onRetry).toHaveBeenCalled();
  });

  // Someone else's turn is not yours to restart.
  it("offers nothing when the caller supplies no retry", async () => {
    vi.useFakeTimers();
    render(<TurnStatus log={streaming()} />);
    await act(async () => {
      vi.advanceTimersByTime(90_000);
    });
    expect(screen.queryByTestId("turn-retry")).not.toBeInTheDocument();
  });
});

/**
 * A send can be rejected by a gateway BEFORE it reaches the app. No turn ever
 * runs, so nothing is persisted and no terminal event can arrive — the deliberate
 * "stay streaming, the turn may be running" tolerance then waits forever. The
 * retry button is an exit, but only if the user thinks to press it; the wait
 * itself has to stop claiming to be a wait.
 */
describe("TurnStatus — a wait that gave up", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("keeps waiting for as long as a turn plausibly takes", async () => {
    vi.useFakeTimers();
    render(<TurnStatus log={streaming()} />);
    await act(async () => {
      vi.advanceTimersByTime(120_000);
    });
    expect(screen.queryByTestId("turn-abandoned")).not.toBeInTheDocument();
  });

  it("stops claiming to be waiting once nothing has happened for far too long", async () => {
    vi.useFakeTimers();
    render(<TurnStatus log={streaming()} />);
    await act(async () => {
      vi.advanceTimersByTime(11 * 60_000);
    });
    expect(screen.getByTestId("turn-abandoned")).toBeInTheDocument();
  });

  // Output means the turn is real and running; length is not a reason to
  // declare it lost.
  it("does not give up on a turn that is visibly producing output", async () => {
    vi.useFakeTimers();
    const answering = fold(
      [{ type: "message_delta", text: "still going" } as AgentEvent],
      streaming(),
    );
    render(<TurnStatus log={answering} />);
    await act(async () => {
      vi.advanceTimersByTime(11 * 60_000);
    });
    expect(screen.queryByTestId("turn-abandoned")).not.toBeInTheDocument();
  });
});
