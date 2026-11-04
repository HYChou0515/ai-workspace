// @vitest-environment happy-dom
/**
 * A long chat must not get slower per streamed token (the long-chat perf fix).
 *
 * Measured in a real browser on a 300-turn chat: every streamed delta
 * re-rendered every message and re-parsed its markdown, so the main thread was
 * busy for nearly the whole answer and froze for over a second at a time. The
 * reducer already keeps a finished entry's object as it was (`reduceAgent`
 * copies the array, replaces only the live entry), so a finished message has
 * nothing new to draw — these tests pin that it is not drawn again.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Count what is expensive: each markdown parse of a message body. Recorded by
// the body text, so the test can tell a finished message from the live one.
const parsed: string[] = [];
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: ReactNode }) => {
    parsed.push(String(children));
    return <div>{children}</div>;
  },
}));

// And each row drawn — the row-level guard. `AnswerMarkdown`'s memo would hide
// a row that is redrawn for nothing (its markdown is not parsed again), so the
// rows themselves are counted too: `ChatFeedRow`'s memo, `useStableActions`
// and `useSameShape` each keep a finished row from being drawn at all.
const drawn: string[] = [];
vi.mock("../../components/AgentEntryView", async (importOriginal) => {
  const real = await importOriginal<typeof import("../../components/AgentEntryView")>();
  return {
    ...real,
    EntryView: (props: Parameters<typeof real.EntryView>[0]) => {
      const e = props.entry as { kind: string; message?: { content?: string } };
      drawn.push(e.kind === "message" ? String(e.message?.content) : e.kind);
      return <real.EntryView {...props} />;
    },
  };
});

import { api } from "../../api";
import { kbApi } from "../../api/kb";
import type { AgentState } from "../../hooks/useAgent";
import { QueryWrap, makeTestQueryClient } from "../../test/queryWrapper";
import type { AgentEntry, AgentLog } from "./agentLog";
import { AgentPanel } from "./AgentPanel";

function answer(text: string, live = false): AgentEntry {
  return {
    kind: "message",
    at: 1,
    live,
    message: { role: "assistant", content: text },
  } as unknown as AgentEntry;
}

function question(text: string): AgentEntry {
  return { kind: "message", at: 1, message: { role: "user", content: text, author: "u" } } as unknown as AgentEntry;
}

function agentWith(log: AgentLog): AgentState {
  return {
    investigationId: "it1",
    log,
    connection: { state: "live", receiving: true, error: null, attempts: 0 },
    send: vi.fn(async () => {}),
    mention: vi.fn(async () => {}),
    cancel: vi.fn(),
    undo: vi.fn(async () => {}),
  } as unknown as AgentState;
}

function panel(agent: AgentState) {
  return (
    <AgentPanel
      investigationId="it1"
      chatId="chat-1"
      agent={agent}
      picker={[]}
      suggestions={[]}
      attachedPreset=""
      onAttachPreset={() => {}}
      uploadDir="uploads"
    />
  );
}

beforeEach(() => {
  parsed.length = 0;
  vi.spyOn(kbApi, "listCollections").mockResolvedValue([]);
  vi.spyOn(api, "getWorkspaceUsage").mockResolvedValue({ used: 0, quota: 0 });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("a streamed token in a long chat", () => {
  it("re-parses only the answer being streamed, not the finished ones", () => {
    const finished: AgentEntry[] = [];
    for (let i = 0; i < 20; i++) finished.push(question(`q${i}`), answer(`finished answer ${i}`));
    const before: AgentLog = {
      ...({} as AgentLog),
      entries: [...finished, question("now"), answer("par", true)],
      streaming: true,
    } as AgentLog;
    const client = makeTestQueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryWrap client={client}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryWrap>
    );
    const view = render(panel(agentWith(before)), { wrapper });
    expect(parsed.filter((t) => t.startsWith("finished answer"))).toHaveLength(20);

    // What `reduceAgent` hands back for the next delta: a new array, the same
    // objects for every finished entry, a new one only for the live answer.
    parsed.length = 0;
    drawn.length = 0;
    const entries = [...before.entries];
    entries[entries.length - 1] = answer("partial", true);
    view.rerender(panel(agentWith({ ...before, entries } as AgentLog)));

    expect(parsed.filter((t) => t.startsWith("finished answer"))).toEqual([]);
    expect(parsed).toContain("partial");
    // Not drawn at all: only the live answer's row is.
    expect(drawn).toEqual(["partial"]);
  });

  it("re-parses no finished answer when a turn starts or ends", () => {
    // A turn starting or ending flips `streaming`, which withdraws or offers
    // replay / undo on every row — a change to each row's buttons, not to its
    // text. Before, that redrew every answer's markdown twice per turn: on the
    // 300-turn chat, one main-thread task of 1.2–2s at the start of each turn.
    const finished: AgentEntry[] = [];
    for (let i = 0; i < 20; i++) finished.push(question(`q${i}`), answer(`finished answer ${i}`));
    const idle = { entries: finished, streaming: false } as unknown as AgentLog;
    const client = makeTestQueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryWrap client={client}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryWrap>
    );
    const view = render(panel(agentWith(idle)), { wrapper });

    parsed.length = 0;
    view.rerender(panel(agentWith({ ...idle, streaming: true } as AgentLog)));
    view.rerender(panel(agentWith(idle)));

    expect(parsed.filter((t) => t.startsWith("finished answer"))).toEqual([]);
  });

  it("re-parses no finished answer when the thread is fetched again after a turn", () => {
    // When a turn ends the thread is fetched again and rebuilt
    // (`reconcileSnapshot` → `logFromMessages`): every entry is a NEW object,
    // with a new `citations` array, and the same text. Seen in the demo on the
    // 300-turn chat as one 1.4s freeze at the end of every answer.
    const withCitations = (e: AgentEntry): AgentEntry =>
      e.kind === "message" ? ({ ...e, message: { ...e.message, citations: [] } } as AgentEntry) : e;
    const finished: AgentEntry[] = [];
    for (let i = 0; i < 20; i++) finished.push(question(`q${i}`), answer(`finished answer ${i}`));
    const before = { entries: finished.map(withCitations), streaming: false } as unknown as AgentLog;
    const client = makeTestQueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryWrap client={client}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryWrap>
    );
    const view = render(panel(agentWith(before)), { wrapper });

    parsed.length = 0;
    const refetched = { ...before, entries: finished.map(withCitations) } as AgentLog;
    view.rerender(panel(agentWith(refetched)));

    expect(parsed.filter((t) => t.startsWith("finished answer"))).toEqual([]);
  });
});

