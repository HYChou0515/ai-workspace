// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EntryView } from "./AgentEntryView";

// UserChip pulls /users/{id} — stub so happy-dom doesn't hit the network.
vi.mock("./UserChip", () => ({
  UserChip: ({ userId }: { userId: string }) => <span data-chip={userId} />,
}));
vi.mock("./RcaMark", () => ({
  RcaMark: () => <span data-rca />,
}));
vi.mock("./Icon", () => ({
  Icon: ({ name }: { name: string }) => <span data-icon={name} />,
}));

afterEach(cleanup);

describe("EntryView — ask_knowledge_base tool card citations", () => {
  it("renders citation cards under an ask_knowledge_base tool call (RCA reload path)", () => {
    // The persisted RCA tool message (BE attaches citations) hydrates into a
    // ToolCallView with `citations` set; the tool card surfaces them as
    // reference cards — same UX as direct KB chat's answer cards.
    render(
      <EntryView
        entry={{
          kind: "tool_call",
          call: {
            call_id: "c1",
            name: "ask_knowledge_base",
            args: { question: "why drift?" },
            status: "done",
            output: "answer with [1]",
            citations: [
              {
                marker: 1,
                collection_id: "col",
                document_id: "doc",
                filename: "reflow-spec.md",
                start: 0,
                end: 50,
                source_chunk_ids: ["ck"],
                snippet: "Zone 3 setpoint…",
              },
            ],
          },
        }}
      />,
    );
    expect(screen.getByText("reflow-spec.md")).toBeInTheDocument();
    expect(screen.getByText("[1]")).toBeInTheDocument();
    expect(screen.getByText(/Zone 3 setpoint/)).toBeInTheDocument();
  });

  it("clicking a citation fires onOpenCitation with the picked citation", () => {
    const cite = {
      marker: 1,
      collection_id: "col",
      document_id: "doc",
      filename: "reflow-spec.md",
      start: 0,
      end: 50,
      source_chunk_ids: ["ck"],
      snippet: "snip",
    };
    const onOpen = vi.fn();
    render(
      <EntryView
        entry={{
          kind: "tool_call",
          call: {
            call_id: "c1",
            name: "ask_knowledge_base",
            args: {},
            status: "done",
            output: "answer",
            citations: [cite],
          },
        }}
        onOpenCitation={onOpen}
      />,
    );
    fireEvent.click(screen.getByText("reflow-spec.md"));
    expect(onOpen).toHaveBeenCalledWith(cite);
  });

  it("a non-ask_kb tool call with no citations renders no Sources block", () => {
    // Defensive: tools other than ask_knowledge_base never have citations,
    // and the BE never attaches them. The card shouldn't show a stray
    // "Sources" header just because the field exists in the type.
    render(
      <EntryView
        entry={{
          kind: "tool_call",
          call: {
            call_id: "c2",
            name: "exec",
            args: { cmd: ["echo", "hi"] },
            status: "done",
            output: "hi",
          },
        }}
      />,
    );
    expect(screen.queryByText(/來源|Sources/)).not.toBeInTheDocument();
  });
});

describe("EntryView — de-jargoned tool cards (#160)", () => {
  const toolEntry = (
    name: string,
    args: Record<string, unknown>,
    over: Record<string, unknown> = {},
  ) =>
    ({
      kind: "tool_call",
      call: { call_id: "c", name, args, status: "done", output: "ok", ...over },
    }) as const;

  it("shows a plain-language label + humanized primary arg, not name(args)", () => {
    render(<EntryView entry={toolEntry("exec", { cmd: ["pytest", "-q"] })} />);
    expect(screen.getByText("執行指令")).toBeInTheDocument();
    expect(screen.getByText(/pytest -q/)).toBeInTheDocument();
    expect(screen.queryByText(/exec\(/)).not.toBeInTheDocument();
  });

  it("humanizes the query for a knowledge-base lookup", () => {
    render(<EntryView entry={toolEntry("ask_knowledge_base", { question: "why drift?" })} />);
    expect(screen.getByText("查詢知識庫")).toBeInTheDocument();
    expect(screen.getByText(/why drift\?/)).toBeInTheDocument();
  });

  it("falls back to a generic label for an unmapped tool (no raw name leaks)", () => {
    render(<EntryView entry={toolEntry("some_new_tool", { x: 1 })} />);
    expect(screen.getByText("使用工具")).toBeInTheDocument();
    expect(screen.queryByText(/some_new_tool/)).not.toBeInTheDocument();
  });

  it("labels the output as 結果 (done) / 執行中… (running)", () => {
    const { rerender } = render(<EntryView entry={toolEntry("read_file", { path: "a.py" })} />);
    expect(screen.getByText(/結果/)).toBeInTheDocument();
    rerender(
      <EntryView
        entry={toolEntry("read_file", { path: "a.py" }, { status: "running", liveOutput: "…", output: undefined })}
      />,
    );
    expect(screen.getByText(/執行中…/)).toBeInTheDocument();
  });
});

describe("EntryView — replay entry points (#51 P6)", () => {
  it("an assistant answer offers a replay affordance when the surface provides one", () => {
    const onReplay = vi.fn();
    render(
      <EntryView
        entry={{
          kind: "message",
          message: { role: "assistant", content: "Zone 3 ran hot." },
        }}
        onReplay={onReplay}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /重跑/ }));
    expect(onReplay).toHaveBeenCalled();
  });

  it("a tool card offers the same affordance", () => {
    const onReplay = vi.fn();
    render(
      <EntryView
        entry={{
          kind: "tool_call",
          call: { call_id: "c1", name: "read_file", args: {}, status: "done", output: "412C" },
        }}
        onReplay={onReplay}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /重跑/ }));
    expect(onReplay).toHaveBeenCalled();
  });

  it("no affordance for user messages or when the surface opts out", () => {
    render(
      <EntryView
        entry={{ kind: "message", message: { role: "user", content: "hi" } }}
        onReplay={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /重跑/ })).not.toBeInTheDocument();
    cleanup();
    render(
      <EntryView
        entry={{ kind: "message", message: { role: "assistant", content: "yo" } }}
      />,
    );
    expect(screen.queryByRole("button", { name: /重跑/ })).not.toBeInTheDocument();
  });
});

describe("EntryView — undo affordance (#38)", () => {
  it("a user message offers an undo-to-here control when the surface provides one", () => {
    const onUndo = vi.fn();
    render(
      <EntryView
        entry={{ kind: "message", message: { role: "user", content: "why?" } }}
        onUndo={onUndo}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /復原/ }));
    expect(onUndo).toHaveBeenCalled();
  });

  it("keeps the undo icon always visible but reveals a compact text label on hover (#172)", () => {
    render(
      <EntryView
        entry={{ kind: "message", message: { role: "user", content: "why?" } }}
        onUndo={vi.fn()}
      />,
    );
    const btn = screen.getByRole("button", { name: /復原/ }); // aria-label stays descriptive
    expect(within(btn).queryByText("復原此回合之後")).not.toBeInTheDocument();
    fireEvent.mouseEnter(btn);
    expect(within(btn).getByText("復原此回合之後")).toBeInTheDocument();
    fireEvent.mouseLeave(btn);
    expect(within(btn).queryByText("復原此回合之後")).not.toBeInTheDocument();
  });

  it("no undo control on assistant messages or when the surface opts out", () => {
    render(
      <EntryView
        entry={{ kind: "message", message: { role: "assistant", content: "because" } }}
        onUndo={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /復原/ })).not.toBeInTheDocument();
    cleanup();
    render(<EntryView entry={{ kind: "message", message: { role: "user", content: "hi" } }} />);
    expect(screen.queryByRole("button", { name: /復原/ })).not.toBeInTheDocument();
  });
});

describe("EntryView — repetition stop notice (#113)", () => {
  it("shows a notice on an assistant answer truncated for repetition", () => {
    render(
      <EntryView
        entry={{ kind: "message", at: 0, message: { role: "assistant", content: "Good answer.", stopped_reason: "repetition" } }}
      />,
    );
    expect(screen.getByText("Good answer.")).toBeInTheDocument();
    expect(screen.getByText(/重複/)).toBeInTheDocument();
  });

  it("shows a thinking-loop notice when the model never produced an answer", () => {
    render(
      <EntryView
        entry={{ kind: "message", at: 0, message: { role: "assistant", content: "", stopped_reason: "repetition" } }}
      />,
    );
    expect(screen.getByText(/思考/)).toBeInTheDocument();
  });

  it("renders no notice for a normal answer", () => {
    render(
      <EntryView
        entry={{ kind: "message", at: 0, message: { role: "assistant", content: "All good." } }}
      />,
    );
    expect(screen.queryByText(/重複/)).not.toBeInTheDocument();
  });
});

describe("EntryView — live thinking (reasoning block)", () => {
  const assistant = (over: { reasoning?: string; content?: string }) => ({
    kind: "message" as const,
    message: { role: "assistant" as const, author: "Agent", content: over.content ?? "", reasoning: over.reasoning },
  });

  it("auto-expands the streaming thoughts while the answer hasn't started", () => {
    const { container } = render(<EntryView entry={assistant({ reasoning: "work through it" })} />);
    const details = container.querySelector("details");
    expect(details).toHaveAttribute("open");
    expect(screen.getByText(/思考中/)).toBeInTheDocument();
    // the actual thoughts are on screen — not a blank page behind a toggle
    expect(screen.getByText(/work through it/)).toBeInTheDocument();
  });

  it("collapses to a Chinese 已思考 summary once the answer streams (no English)", () => {
    const { container } = render(
      <EntryView entry={assistant({ reasoning: "thought", content: "Here is the answer." })} />,
    );
    expect(container.querySelector("details")).not.toHaveAttribute("open");
    expect(screen.getByText(/已思考/)).toBeInTheDocument();
    expect(screen.queryByText(/Show thinking/i)).not.toBeInTheDocument();
  });

  it("auto-collapses and stamps the elapsed think time when the answer begins", () => {
    vi.useFakeTimers();
    try {
      const { container, rerender } = render(<EntryView entry={assistant({ reasoning: "ponder" })} />);
      expect(container.querySelector("details")).toHaveAttribute("open");
      vi.advanceTimersByTime(8_000);
      rerender(<EntryView entry={assistant({ reasoning: "ponder", content: "Done." })} />);
      expect(container.querySelector("details")).not.toHaveAttribute("open");
      expect(screen.getByText(/已思考 8s/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("EntryView — workflow step/phase lines (#100 observability)", () => {
  it("renders a running step with its name and key so a deterministic phase shows movement", () => {
    render(
      <EntryView
        entry={{ kind: "step", step: { phase: "commit", name: "ingest", key: "report.md", status: "running" } }}
      />,
    );
    const line = screen.getByTestId("wf-step");
    expect(line).toHaveAttribute("data-status", "running");
    expect(line).toHaveTextContent("ingest");
    expect(line).toHaveTextContent("report.md");
  });

  it("a failed step surfaces its reason", () => {
    render(
      <EntryView
        entry={{ kind: "step", step: { phase: "classify", name: "classify_a", status: "failed", reason: "bad collection" } }}
      />,
    );
    const line = screen.getByTestId("wf-step");
    expect(line).toHaveAttribute("data-status", "failed");
    expect(line).toHaveTextContent("bad collection");
  });

  it("renders a phase divider", () => {
    render(<EntryView entry={{ kind: "phase", phase: "commit" }} />);
    expect(screen.getByTestId("wf-phase")).toHaveTextContent("commit");
  });
});
