// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";

import { realApi } from "./real";

/** A specstar entry whose LATEST REVISION was written by somebody other than
 *  the person who created the resource. `revision_info.created_by` is the
 *  author of that revision; the resource's creator lives in `meta`. */
const EDITED_BY_ADMIN = {
  data: { title: "A's item", owner: "alice" },
  meta: { created_by: "alice", created_time: "t0", updated_time: "t1" },
  revision_info: {
    uid: "u",
    resource_id: "rca-investigation:1",
    revision_id: "r2",
    created_time: "t1",
    updated_time: "t1",
    created_by: "admin",
    updated_by: "admin",
  },
};

function serve(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })),
  );
}

describe("an item's owner is whoever CREATED it, not whoever last edited it", () => {
  afterEach(() => vi.unstubAllGlobals());

  // `useItemAccess` decides "is the current user the owner" from
  // `item.created_by`, and every verb short-circuits on that. Reading the
  // revision author there meant one edit by an admin — a preset pick, a
  // description tweak, anything that writes a revision — turned the creator
  // into a stranger on their own restricted item: no `read_chat` grant, so the
  // page drew the 🔒 locked row and "request access", while the backend (which
  // reads the real creator) would have let them straight in.

  it("listAppItems reads the creator from meta", async () => {
    serve([EDITED_BY_ADMIN]);
    const [item] = await realApi.listAppItems("/rca-investigation");
    expect(item.created_by).toBe("alice");
  });

  it("getAppItem reads the creator from meta", async () => {
    serve(EDITED_BY_ADMIN);
    const item = await realApi.getAppItem("/rca-investigation", "rca-investigation:1");
    expect(item.created_by).toBe("alice");
  });
});
