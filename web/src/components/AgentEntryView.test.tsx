// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
    expect(screen.queryByText(/Sources/)).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: /replay/i }));
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
    fireEvent.click(screen.getByRole("button", { name: /replay/i }));
    expect(onReplay).toHaveBeenCalled();
  });

  it("no affordance for user messages or when the surface opts out", () => {
    render(
      <EntryView
        entry={{ kind: "message", message: { role: "user", content: "hi" } }}
        onReplay={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /replay/i })).not.toBeInTheDocument();
    cleanup();
    render(
      <EntryView
        entry={{ kind: "message", message: { role: "assistant", content: "yo" } }}
      />,
    );
    expect(screen.queryByRole("button", { name: /replay/i })).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: /undo/i }));
    expect(onUndo).toHaveBeenCalled();
  });

  it("no undo control on assistant messages or when the surface opts out", () => {
    render(
      <EntryView
        entry={{ kind: "message", message: { role: "assistant", content: "because" } }}
        onUndo={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /undo/i })).not.toBeInTheDocument();
    cleanup();
    render(<EntryView entry={{ kind: "message", message: { role: "user", content: "hi" } }} />);
    expect(screen.queryByRole("button", { name: /undo/i })).not.toBeInTheDocument();
  });
});
