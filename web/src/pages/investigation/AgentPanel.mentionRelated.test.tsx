// @vitest-environment happy-dom
/**
 * The @-mention picker lists the people who have spoken in this chat first
 * (docs/plan-people-picker-ranking.md D4) — passed as the picker's `related`.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const pickerProps: Array<{ related?: string[] }> = [];
vi.mock("../../components/UserPicker", () => ({
  UserPicker: (props: { related?: string[] }) => {
    pickerProps.push(props);
    return <div data-testid="userpicker" />;
  },
}));

import { api } from "../../api";
import { kbApi } from "../../api/kb";
import type { AgentState } from "../../hooks/useAgent";
import { renderWithQuery } from "../../test/queryWrapper";
import type { AgentEntry, AgentLog } from "./agentLog";
import { AgentPanel } from "./AgentPanel";

const said = (author: string, content: string): AgentEntry =>
  ({ kind: "message", at: 1, message: { role: "user", content, author } }) as unknown as AgentEntry;
const answered = (content: string): AgentEntry =>
  ({ kind: "message", at: 1, message: { role: "assistant", content, author: "agent" } }) as unknown as AgentEntry;

function agentWith(entries: AgentEntry[]): AgentState {
  return {
    investigationId: "it1",
    log: { entries, streaming: false } as unknown as AgentLog,
    connection: { state: "live", receiving: true, error: null, attempts: 0 },
    send: vi.fn(async () => {}),
    mention: vi.fn(async () => {}),
    cancel: vi.fn(),
    undo: vi.fn(async () => {}),
  } as unknown as AgentState;
}

beforeEach(() => {
  pickerProps.length = 0;
  vi.spyOn(kbApi, "listCollections").mockResolvedValue([]);
  vi.spyOn(api, "getWorkspaceUsage").mockResolvedValue({ used: 0, quota: 0 });
  vi.spyOn(api, "getCurrentUser").mockResolvedValue("me");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the @-mention picker", () => {
  it("is given the chat's speakers as its related people — not the viewer, not the agent", async () => {
    const entries = [said("amy", "hi"), answered("hello"), said("ben", "and me"), said("amy", "again"), said("me", "x")];
    renderWithQuery(
      <MemoryRouter>
        <AgentPanel
          investigationId="it1"
          chatId="chat-1"
          agent={agentWith(entries)}
          picker={[]}
          suggestions={[]}
          attachedPreset=""
          onAttachPreset={() => {}}
          uploadDir="uploads"
        />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByTitle(/mention someone/i));
    await screen.findByTestId("userpicker");
    const related = pickerProps.at(-1)?.related ?? [];
    expect([...related].sort()).toEqual(["amy", "ben"]);
  });
});
