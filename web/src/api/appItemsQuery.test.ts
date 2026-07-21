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

// The item PUT was a FULL REPLACEMENT (specstar's own route says so: "replacing
// it entirely"), fed from a CACHED copy of the item. Two consequences:
//
//   * `permission` was stripped to stop a stale copy reverting a share — but
//     under replace semantics an omitted field is written as its default, and
//     `WorkItemBase.permission` defaults to None, which the backend reads as
//     PUBLIC. Saving settings on a private item published it.
//   * every OTHER field was echoed back from the same stale cache, so a save
//     also reverted whatever anyone else had changed since.
//
// A field edit is a partial update, so it must go over PATCH — which specstar
// has had all along (`tests/api/test_investigation_update.py` already claims
// this is how the FE edits items). Omitted then means "leave it alone".
describe("patchAppItemFields", () => {
  afterEach(() => vi.unstubAllGlobals());

  function requestSpy() {
    const seen: { url: string; method?: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        seen.push({
          url: String(input),
          method: init?.method,
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
        return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
      }),
    );
    return seen;
  }

  it("PATCHes only the named fields, so nothing it did not mention can change", async () => {
    const seen = requestSpy();
    await realApi.patchAppItemFields("/rca-investigation", "INC-1", { status: "open" });

    expect(seen[0].method).toBe("PATCH");
    expect(seen[0].url).toContain("/rca-investigation/INC-1");
    expect(seen[0].body).toEqual([{ op: "replace", path: "/status", value: "open" }]);
  });

  it("emits one op per field, in the caller's order", async () => {
    const seen = requestSpy();
    await realApi.patchAppItemFields("/rca-investigation", "INC-1", {
      title: "Reflow drift",
      severity: "P0",
    });
    expect(seen[0].body).toEqual([
      { op: "replace", path: "/title", value: "Reflow drift" },
      { op: "replace", path: "/severity", value: "P0" },
    ]);
  });

  // Still stripped, but now it MEANS "leave it alone" instead of "clear it".
  // `permission` has its own endpoint; the generic field editor must never be a
  // second writer of it, whether it would widen or narrow access.
  it("never carries `permission`, nor the immutable server-owned metadata", async () => {
    const seen = requestSpy();
    await realApi.patchAppItemFields("/rca-investigation", "INC-1", {
      title: "Reflow drift",
      permission: { visibility: "private" },
      resource_id: "INC-1",
      created_time: "2026-06-15T08:00:00Z",
      updated_time: "2026-06-20T12:00:00Z",
      created_by: "alice",
    });

    expect(seen[0].body).toEqual([{ op: "replace", path: "/title", value: "Reflow drift" }]);
  });

  it("sends nothing at all when there is nothing to change", async () => {
    const seen = requestSpy();
    await realApi.patchAppItemFields("/rca-investigation", "INC-1", { permission: {} });
    expect(seen).toHaveLength(0);
  });
});
