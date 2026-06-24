// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";

import { realApi } from "./real";

function fetchSpy(body: string) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return new Response(body, {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return calls;
}

describe("countAppItems", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("GETs /{route}/count with the filter and returns the server count", async () => {
    const calls = fetchSpy("7");
    const n = await realApi.countAppItems("/rca-investigation", {
      data_conditions: '[{"field_path":"status","operator":"eq","value":"done"}]',
    });
    expect(n).toBe(7);
    expect(calls[0]).toContain("/rca-investigation/count?");
    expect(calls[0]).toContain("data_conditions=");
  });
});

/** A specstar list/get entry: domain fields under `data`, the always-present
 * created/updated who+when under `revision_info`. */
function entry() {
  return {
    data: { title: "Reflow drift", owner: "alice" },
    revision_info: {
      uid: "u1",
      resource_id: "INC-1",
      revision_id: "rev-1",
      created_time: "2026-06-15T08:00:00Z",
      updated_time: "2026-06-20T12:00:00Z",
      created_by: "alice",
      updated_by: "bob",
    },
  };
}

describe("listAppItems", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("surfaces created_time and created_by from revision_info onto each item", async () => {
    fetchSpy(JSON.stringify([entry()]));
    const [item] = await realApi.listAppItems("/rca-investigation");
    expect(item.created_time).toBe("2026-06-15T08:00:00Z");
    expect(item.created_by).toBe("alice");
  });
});

describe("getAppItem", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("surfaces created_time and created_by from revision_info", async () => {
    fetchSpy(JSON.stringify(entry()));
    const item = await realApi.getAppItem("/rca-investigation", "INC-1");
    expect(item.created_time).toBe("2026-06-15T08:00:00Z");
    expect(item.created_by).toBe("alice");
  });
});

/** Capture the body of the next write so we can assert what hits the wire. */
function bodySpy() {
  const bodies: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      bodies.push(init?.body ? JSON.parse(String(init.body)) : {});
      return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
    }),
  );
  return bodies;
}

describe("updateAppItem", () => {
  afterEach(() => vi.unstubAllGlobals());

  // #201: getAppItem flattens the server-owned revision metadata onto the item,
  // so a read-modify-write (the model picker, inline severity/status edits)
  // would echo `resource_id` back in the PUT body — and specstar 422s on that
  // ("resource_id … is immutable"), silently dropping every item-field write.
  // updateAppItem must send only the model struct fields.
  it("strips server-generated metadata from the PUT body but keeps the edit", async () => {
    const bodies = bodySpy();
    const item = {
      resource_id: "INC-1",
      created_time: "2026-06-15T08:00:00Z",
      updated_time: "2026-06-20T12:00:00Z",
      created_by: "alice",
      title: "Reflow drift",
      owner: "alice",
      attached_preset: "",
    };
    await realApi.updateAppItem("/rca-investigation", "INC-1", {
      ...item,
      attached_preset: "claude-opus",
    });

    const sent = bodies[0]!;
    expect(sent).not.toHaveProperty("resource_id");
    expect(sent).not.toHaveProperty("created_time");
    expect(sent).not.toHaveProperty("updated_time");
    expect(sent).not.toHaveProperty("created_by");
    // The model fields — including the edited one — still go through.
    expect(sent.attached_preset).toBe("claude-opus");
    expect(sent.title).toBe("Reflow drift");
    expect(sent.owner).toBe("alice");
  });
});
