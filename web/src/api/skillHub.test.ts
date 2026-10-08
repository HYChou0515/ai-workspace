// @vitest-environment happy-dom
/**
 * The skill hub client (`docs/plan-skill-hub.md`): what a refusal READS as.
 *
 * Every write here can be refused with a sentence the server composed for the
 * person — "already has alice's '.skill/…'", "bob already publishes a skill
 * named …", "only the owner may manage this entry". Review round 1 found the
 * client throwing `install failed: 409` instead: `httpErrorFrom` keeps a
 * string `detail` nowhere, so the panel and the page rendered the status,
 * never the reason, and the vitest doubles hid it by throwing the sentence
 * themselves. These run the REAL client over a stubbed fetch.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "./http";
import { skillHubApi } from "./skillHub";

function answering(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status,
          headers: { "content-type": "application/json" },
        }),
    ),
  );
}

afterEach(() => vi.unstubAllGlobals());

const SENTENCE = "this workspace already has alice's '.skill/triage/' — remove or rename that folder first";

describe("skillHubApi refusals", () => {
  it("install: the 409's sentence is the error's message", async () => {
    answering(409, { detail: SENTENCE });
    const err = await skillHubApi.install("rca", "i1", "e1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(409);
    expect((err as HttpError).message).toBe(SENTENCE);
  });

  it.each([
    ["unpublish", () => skillHubApi.unpublish("e1")],
    ["republish", () => skillHubApi.republish("e1")],
    ["remove", () => skillHubApi.remove("e1")],
    ["transfer", () => skillHubApi.transfer("e1", "bob")],
    ["edit", () => skillHubApi.edit("e1")],
    [
      "setPermission",
      () =>
        skillHubApi.setPermission("e1", {
          visibility: "private",
          read_meta: [],
          write_meta: [],
          read_content: [],
          add_content: [],
          edit_content: [],
          read_chat: [],
          converse: [],
          execute: [],
          use_terminal: [],
          change_permission: [],
        }),
    ],
    ["list", () => skillHubApi.list()],
    ["get", () => skillHubApi.get("e1")],
    ["history", () => skillHubApi.history("e1")],
    ["version", () => skillHubApi.version("e1", "e1:2")],
    ["versionFile", () => skillHubApi.versionFile("e1", "e1:2", "a.md")],
    ["diff", () => skillHubApi.diff("e1", "e1:1", "e1:2")],
    ["rollback", () => skillHubApi.rollback("e1", "e1:1", "c2")],
    ["fork", () => skillHubApi.fork("rca", "i1", "e1", "e1:1")],
  ])("%s: a refusal's sentence is the message", async (_name, call) => {
    answering(403, { detail: "only the owner may manage this entry" });
    const err = await call().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).message).toBe("only the owner may manage this entry");
  });

  it("a coded refusal keeps the code and its parameters for the page to word (plan-skill-hub-ui-polish D16)", async () => {
    answering(409, {
      detail: {
        error: "folder_in_the_way",
        owner: "alice",
        path: ".skill/triage/",
      },
    });
    const err = await skillHubApi
      .install("rca", "i1", "e1")
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).code).toBe("folder_in_the_way");
    expect((err as HttpError).detail).toEqual({
      error: "folder_in_the_way",
      owner: "alice",
      path: ".skill/triage/",
    });
    // No sentence came; the message is the fallback, never "[object Object]".
    expect((err as HttpError).message).toMatch(/install.*409/);
  });

  it("a refusal with no sentence still says what failed and the status", async () => {
    answering(500, {});
    const err = await skillHubApi.install("rca", "i1", "e1").catch((e: unknown) => e);
    expect((err as HttpError).message).toMatch(/install.*500/);
  });
});

describe("skillHubApi history (plan-skill-hub-history §8)", () => {
  function recording(body: unknown) {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );
    return calls;
  }

  it("browses with only the parameters that are not the default, and keeps the total and counting day", async () => {
    const calls = recording({ entries: [], total: 7, counted_since: "2026-10-07" });
    const page = await skillHubApi.browse({
      q: "deck",
      mine: true,
      owner: "bob",
      sort: "updated",
      offset: 50,
      limit: 50,
      forks: true,
    });
    await skillHubApi.browse({});
    expect(calls.map((c) => c.url)).toEqual([
      "/api/skill-hub/entries?q=deck&mine=true&owner=bob&sort=updated&offset=50&limit=50&forks=true",
      "/api/skill-hub/entries",
    ]);
    expect(page).toEqual({ entries: [], total: 7, counted_since: "2026-10-07" });
  });

  it("reads an older API pod's answers mid-rollout: no total, files as bare names", async () => {
    // An API pod from before the redo answers the list without `total` and
    // the detail / a version with `files: string[]`. A total of 0 drew the
    // "nobody has published anything" state over a full list, and a bare
    // name broke the files tab.
    recording({ entries: [{ id: "e-1" }, { id: "e-2" }], counted_since: "" });
    expect((await skillHubApi.browse({})).total).toBe(2);
    recording({ id: "e-1", files: ["SKILL.md", "a.md"], skill_md: "" });
    expect((await skillHubApi.get("e-1")).files).toEqual([
      { path: "SKILL.md", size: null },
      { path: "a.md", size: null },
    ]);
    recording({ revision: "r", files: ["SKILL.md"], skill_md: "" });
    expect((await skillHubApi.version("e-1", "r")).files).toEqual([{ path: "SKILL.md", size: null }]);
  });

  it("asks for where the entry is installed and where it could go", async () => {
    const calls = recording({ installs: [], missing_tools: [], items: [] });
    await skillHubApi.installs("e/1");
    await skillHubApi.targets("e/1", "rca");
    expect(calls.map((c) => c.url)).toEqual([
      "/api/skill-hub/entries/e%2F1/installs",
      "/api/skill-hub/entries/e%2F1/targets?app=rca",
    ]);
  });

  it("asks for each history read at its route, the revision in the path encoded", async () => {
    const calls = recording({ events: [], files: [] });
    await skillHubApi.history("e1");
    await skillHubApi.version("e1", "e1:2");
    await skillHubApi.versionFile("e1", "e1:2", "references/a b.md");
    await skillHubApi.diff("e1", "e1:1", "e1:2");
    expect(calls.map((c) => c.url)).toEqual([
      "/api/skill-hub/entries/e1/history",
      "/api/skill-hub/entries/e1/versions/e1%3A2",
      "/api/skill-hub/entries/e1/versions/e1%3A2/file?path=references%2Fa+b.md",
      "/api/skill-hub/entries/e1/diff?from=e1%3A1&to=e1%3A2",
    ]);
  });

  it("rolls back against the version the page showed, and forks into an item", async () => {
    const calls = recording({ id: "e1", commit: "c1", name: "triage", missing_tools: [] });
    await skillHubApi.rollback("e1", "e1:1", "c2");
    await skillHubApi.fork("rca", "i1", "e1", "e1:1");
    expect(calls.map((c) => [c.url, c.init?.method, c.init?.body])).toEqual([
      [
        "/api/skill-hub/entries/e1/rollback",
        "POST",
        JSON.stringify({ revision: "e1:1", expected: "c2" }),
      ],
      [
        "/api/a/rca/items/i1/skills/fork",
        "POST",
        JSON.stringify({ entry_id: "e1", revision: "e1:1" }),
      ],
    ]);
  });
});


describe("refreshItemSkill refusals (review round 1)", () => {
  it("a 409's sentence is the error's message, not the raw body", async () => {
    const { realApi } = await import("./real");
    const sentence = "this copy of 'triage' no longer records which version it came from — reset it instead";
    answering(409, { detail: sentence });
    const err = await realApi
      .refreshItemSkill("rca", "i1", "triage", { force: false })
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(409);
    expect((err as HttpError).message).toBe(sentence);
  });
});
