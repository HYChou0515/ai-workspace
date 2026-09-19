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

const SENTENCE =
  "this workspace already has alice's '.skill/triage/' — remove or rename that folder first";

describe("skillHubApi refusals", () => {
  it("install: the 409's sentence is the error's message", async () => {
    answering(409, { detail: SENTENCE });
    const err = await skillHubApi
      .install("rca", "i1", "e1")
      .catch((e: unknown) => e);
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
  ])("%s: a refusal's sentence is the message", async (_name, call) => {
    answering(403, { detail: "only the owner may manage this entry" });
    const err = await call().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).message).toBe(
      "only the owner may manage this entry",
    );
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
    const err = await skillHubApi
      .install("rca", "i1", "e1")
      .catch((e: unknown) => e);
    expect((err as HttpError).message).toMatch(/install.*500/);
  });
});
