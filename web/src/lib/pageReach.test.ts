import { describe, expect, it } from "vitest";

import { pageReach } from "./pageReach";

// `docs/plan-deploy-reach.md` decision 8: who a Deployed page's link opens for,
// decided the way the server decides a `/w/` read — `read_meta`, then
// `read_content` (`perm/authorize.py`).
describe("pageReach", () => {
  it("no permission at all is public (the back-compat rule) — everyone", () => {
    expect(pageReach(undefined)).toBe("everyone");
    expect(pageReach(null)).toBe("everyone");
  });
  it("public — everyone", () => {
    expect(pageReach({ visibility: "public" })).toBe("everyone");
  });
  it("private — the owner, whatever the grant lists say", () => {
    expect(pageReach({ visibility: "private", read_meta: ["all"], read_content: ["all"] })).toBe("owner");
  });
  it("restricted, `all` holding both verbs — everyone", () => {
    expect(pageReach({ visibility: "restricted", read_meta: ["all"], read_content: ["all"] })).toBe("everyone");
  });
  it("restricted, someone holding both verbs — shared", () => {
    expect(
      pageReach({ visibility: "restricted", read_meta: ["user:a"], read_content: ["user:a"] }),
    ).toBe("shared");
    expect(
      pageReach({ visibility: "restricted", read_meta: ["group:g"], read_content: ["group:g"] }),
    ).toBe("shared");
    // `all` sees it exists, one person may read it: that person opens it.
    expect(pageReach({ visibility: "restricted", read_meta: ["all"], read_content: ["user:a"] })).toBe("shared");
  });
  it("restricted, nobody may read the files — the owner (a Discoverable grant opens nothing)", () => {
    expect(pageReach({ visibility: "restricted", read_meta: ["all"] })).toBe("owner");
    expect(pageReach({ visibility: "restricted" })).toBe("owner");
  });
  it("restricted, files granted to someone who may not see the workspace — the owner", () => {
    // The server refuses `read_meta` first (a 404), so a `read_content` grant
    // alone opens nothing.
    expect(pageReach({ visibility: "restricted", read_content: ["user:a"] })).toBe("owner");
    // One person sees it exists, another may read it: neither passes both.
    expect(
      pageReach({ visibility: "restricted", read_meta: ["user:a"], read_content: ["user:b"] }),
    ).toBe("owner");
  });
  it("two different groups — shared (membership is not known here, so they may overlap)", () => {
    expect(
      pageReach({ visibility: "restricted", read_meta: ["group:g1"], read_content: ["group:g2"] }),
    ).toBe("shared");
  });
  it("a user on one verb and a group on the other — shared (the user may be in the group)", () => {
    // Review round 1: the server matches a reader's user AND group subjects,
    // so `user:bob` seeing it and `group:eng` reading it opens it for bob
    // when he is in eng — which this cannot know, so it must not say "owner".
    expect(
      pageReach({ visibility: "restricted", read_meta: ["user:bob"], read_content: ["group:eng"] }),
    ).toBe("shared");
    expect(
      pageReach({ visibility: "restricted", read_meta: ["group:eng"], read_content: ["user:bob"] }),
    ).toBe("shared");
  });
  it("`all` may read, someone sees it exists — shared", () => {
    expect(pageReach({ visibility: "restricted", read_meta: ["user:a"], read_content: ["all"] })).toBe("shared");
  });
  it("a permission this build cannot read — unknown, never a guess", () => {
    expect(pageReach({ visibility: "secret" })).toBe("unknown");
    expect(pageReach("nonsense")).toBe("unknown");
  });
});
