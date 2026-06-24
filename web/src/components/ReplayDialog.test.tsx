// @vitest-environment happy-dom
/**
 * ReplayDialog (#51 P6) — runs one replay (turn or doc) and shows the
 * current model's raw output beside what originally happened. Q4: the
 * replay is a pure probe — copy must make clear nothing in the
 * conversation/document changes; a wanted tool call renders as intent.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReplayApi, ReplayOut } from "../api/health";
import { ReplayError } from "../api/health";
import { renderWithQuery } from "../test/queryWrapper";
import { ReplayDialog } from "./ReplayDialog";

function out(over: Partial<ReplayOut>): ReplayOut {
  return {
    text: "",
    reasoning: "",
    tool_calls: [],
    model: "ollama_chat/qwen3:14b",
    latency_ms: 1234,
    note: "",
    original: null,
    request: null,
    ...over,
  };
}

describe("ReplayDialog", () => {
  afterEach(cleanup);

  it("turn replay shows the original and the fresh answer side by side", async () => {
    const client: ReplayApi = {
      replayTurn: vi.fn(async () =>
        out({
          text: "Zone 3 exceeded its limit.",
          reasoning: "let me think",
          original: {
            role: "assistant",
            content: "Zone 3 ran hot.",
            tool_name: null,
            tool_args: null,
          },
        }),
      ),
      replayDoc: async () => out({}),
    };
    renderWithQuery(
      <ReplayDialog
        request={{ kind: "turn", source: "rca", threadId: "inv-1", messageIndex: 2 }}
        onClose={() => {}}
        client={client}
      />,
    );

    expect(await screen.findByText("Zone 3 ran hot.")).toBeInTheDocument();
    expect(screen.getByText("Zone 3 exceeded its limit.")).toBeInTheDocument();
    expect(client.replayTurn).toHaveBeenCalledWith({
      source: "rca",
      thread_id: "inv-1",
      message_index: 2,
    });
    // Thinking stays collapsed until asked for. #171: zh-TW label 顯示思考.
    expect(screen.queryByText("let me think")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /思考/ }));
    expect(screen.getByText("let me think")).toBeInTheDocument();
  });

  it("shows what the replay sent the model so it can be compared to the live turn", async () => {
    const client: ReplayApi = {
      replayTurn: async () =>
        out({
          text: "answer",
          request: {
            model: "ollama_chat/qwen3:14b",
            endpoint: "proxy:4000",
            tools: ["kb_search"],
            parallel_tool_calls: "unset",
            tool_choice: "auto (unset)",
          },
        }),
      replayDoc: async () => out({}),
    };
    renderWithQuery(
      <ReplayDialog
        request={{ kind: "turn", source: "kb", threadId: "chat-1", messageIndex: 1 }}
        onClose={() => {}}
        client={client}
      />,
    );

    expect(await screen.findByText("answer")).toBeInTheDocument();
    expect(screen.getByText(/proxy:4000/)).toBeInTheDocument();
    expect(screen.getByText(/kb_search/)).toBeInTheDocument();
    expect(screen.getByText(/parallel_tool_calls/)).toBeInTheDocument();
  });

  it("renders a wanted tool call as intent — nothing executes", async () => {
    const client: ReplayApi = {
      replayTurn: async () =>
        out({
          tool_calls: [{ name: "read_file", arguments: { path: "oven.log" } }],
          original: {
            role: "tool",
            content: "412C",
            tool_name: "read_file",
            tool_args: { path: "oven.log" },
          },
        }),
      replayDoc: async () => out({}),
    };
    renderWithQuery(
      <ReplayDialog
        request={{ kind: "turn", source: "rca", threadId: "inv-1", messageIndex: 1 }}
        onClose={() => {}}
        client={client}
      />,
    );

    // Both sides show the call shape; the replay side is marked as a
    // would-be action.
    const calls = await screen.findAllByText(/read_file/);
    expect(calls.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/would call/i)).toBeInTheDocument();
  });

  it("doc replay shows the raw output and the outcome note", async () => {
    const client: ReplayApi = {
      replayTurn: async () => out({}),
      replayDoc: vi.fn(async () =>
        out({
          text: '{"insights": []}',
          note: "no insights would be extracted from this response",
        }),
      ),
    };
    renderWithQuery(
      <ReplayDialog request={{ kind: "doc", documentId: "doc-1" }} onClose={() => {}} client={client} />,
    );

    expect(await screen.findByText(/no insights would be extracted/i)).toBeInTheDocument();
    expect(client.replayDoc).toHaveBeenCalledWith("doc-1");
  });

  it("surfaces the server's explanation when a replay isn't possible", async () => {
    const client: ReplayApi = {
      replayTurn: async () => out({}),
      replayDoc: async () => {
        throw new ReplayError(409, "this document's processing has no AI step to replay");
      },
    };
    renderWithQuery(
      <ReplayDialog request={{ kind: "doc", documentId: "doc-1" }} onClose={() => {}} client={client} />,
    );

    expect(await screen.findByText(/no AI step to replay/i)).toBeInTheDocument();
  });

  it("closes via the close button", async () => {
    const onClose = vi.fn();
    const client: ReplayApi = {
      replayTurn: async () => out({ text: "hi" }),
      replayDoc: async () => out({}),
    };
    renderWithQuery(
      <ReplayDialog
        request={{ kind: "turn", source: "kb", threadId: "chat-1", messageIndex: 1 }}
        onClose={onClose}
        client={client}
      />,
    );
    await userEvent.click(await screen.findByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });
});
