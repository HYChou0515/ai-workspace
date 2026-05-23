import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import type { AgentEvent } from "../events";
import { mockApi } from "./mock";

// Module state in mock.ts persists across tests in this file. Tests that
// mutate (createWorkspace, streamAgentEvents) use unique workspace names so
// they don't bleed into each other.

beforeAll(() => {
  // Real setTimeout makes the suite take 5–10s; fake timers with
  // shouldAdvanceTime keep async/await + setTimeout-based delays moving
  // without manual `vi.advanceTimersByTime` calls.
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterAll(() => {
  vi.useRealTimers();
});

async function drain(
  args: Parameters<typeof mockApi.streamAgentEvents>[0],
): Promise<AgentEvent[]> {
  const out: AgentEvent[] = [];
  for await (const ev of mockApi.streamAgentEvents(args)) out.push(ev);
  return out;
}

describe("mockApi — listWorkspaces / createWorkspace", () => {
  it("lists the two seeded workspaces", async () => {
    const ws = await mockApi.listWorkspaces();
    const ids = ws.map((w) => w.resource_id);
    expect(ids).toContain("ws-readme");
    expect(ids).toContain("ws-empty");
  });

  it("createWorkspace prepends and returns the new workspace", async () => {
    const created = await mockApi.createWorkspace({
      name: "Created in test A",
      description: "from list test",
    });
    expect(created.resource_id).toMatch(/^ws-mock-/);
    expect(created.name).toBe("Created in test A");
    const ws = await mockApi.listWorkspaces();
    expect(ws[0].resource_id).toBe(created.resource_id);
  });

  it("a freshly created workspace starts with an empty file list", async () => {
    const created = await mockApi.createWorkspace({ name: "empty-files-test" });
    const files = await mockApi.listFiles(created.resource_id);
    expect(files).toEqual([]);
  });
});

describe("mockApi — getConversationByWorkspace", () => {
  it("returns the seeded conversation for ws-readme", async () => {
    const conv = await mockApi.getConversationByWorkspace("ws-readme");
    expect(conv).not.toBeNull();
    expect(conv!.workspace_id).toBe("ws-readme");
    expect(conv!.messages.length).toBeGreaterThanOrEqual(3);
  });

  it("returns null for a workspace with no conversation yet", async () => {
    const fresh = await mockApi.createWorkspace({ name: "no-conv-yet" });
    const conv = await mockApi.getConversationByWorkspace(fresh.resource_id);
    expect(conv).toBeNull();
  });

  it("returns a defensive copy — mutating the result doesn't poison state", async () => {
    const first = await mockApi.getConversationByWorkspace("ws-readme");
    const initialLen = first!.messages.length;
    first!.messages.push({ role: "user", content: "POISON" });
    first!.messages[0].content = "MUTATED";

    const second = await mockApi.getConversationByWorkspace("ws-readme");
    expect(second!.messages.length).toBe(initialLen);
    expect(second!.messages[0].content).not.toBe("MUTATED");
  });
});

describe("mockApi — streamAgentEvents keyword dispatch", () => {
  it.each([
    ["happy path (no keyword)", "hello there", "done"],
    ["cancel keyword", "please cancel", "run_cancelled"],
    ["max-turns keyword", "loop forever", "max_turns_exceeded"],
    ["error keyword", "boom", "error"],
  ] as const)("%s ends with %s", async (_label, content, terminal) => {
    const ws = await mockApi.createWorkspace({ name: `dispatch-${terminal}` });
    const events = await drain({ workspaceId: ws.resource_id, content });
    expect(events.at(-1)?.type).toBe(terminal);
  });

  it("idle keyword emits sandbox_killed_idle mid-stream and still ends with done", async () => {
    const ws = await mockApi.createWorkspace({ name: "idle-test" });
    const events = await drain({
      workspaceId: ws.resource_id,
      content: "go idle now",
    });
    const types = events.map((e) => e.type);
    expect(types).toContain("sandbox_killed_idle");
    expect(events.at(-1)?.type).toBe("done");
  });

  it("parse-error keyword emits tool_call_parse_error then recovers to done", async () => {
    const ws = await mockApi.createWorkspace({ name: "parse-test" });
    const events = await drain({
      workspaceId: ws.resource_id,
      content: "parse error please",
    });
    const types = events.map((e) => e.type);
    expect(types).toContain("tool_call_parse_error");
    expect(events.at(-1)?.type).toBe("done");
  });

  it("write keyword grows the file list", async () => {
    const ws = await mockApi.createWorkspace({ name: "write-test" });
    expect(await mockApi.listFiles(ws.resource_id)).toEqual([]);
    await drain({ workspaceId: ws.resource_id, content: "write me a note" });
    const files = await mockApi.listFiles(ws.resource_id);
    expect(files.length).toBe(1);
    expect(files[0].path).toMatch(/^notes\/note-/);
  });
});

describe("mockApi — streamAgentEvents conversation persistence + cancel", () => {
  it("accumulates user + assistant + tool messages into the conversation", async () => {
    const ws = await mockApi.createWorkspace({ name: "persist-test" });
    await drain({ workspaceId: ws.resource_id, content: "hello" });
    const conv = await mockApi.getConversationByWorkspace(ws.resource_id);
    expect(conv).not.toBeNull();
    const roles = conv!.messages.map((m) => m.role);
    expect(roles[0]).toBe("user");
    expect(roles).toContain("assistant");
    expect(roles).toContain("tool");
  });

  it("aborting the signal during iteration yields run_cancelled and stops", async () => {
    const ws = await mockApi.createWorkspace({ name: "abort-test" });
    const ac = new AbortController();
    const events: AgentEvent[] = [];
    const it = mockApi.streamAgentEvents({
      workspaceId: ws.resource_id,
      content: "hello",
      signal: ac.signal,
    });
    // Pull one event, then abort, then drain the rest.
    const first = await it.next();
    if (!first.done) events.push(first.value);
    ac.abort();
    for await (const ev of it) events.push(ev);
    expect(events.at(-1)?.type).toBe("run_cancelled");
  });
});
