import { afterEach, describe, expect, it, vi } from "vitest";

import { realKbApi, UploadBlockedError } from "./kb";

afterEach(() => vi.unstubAllGlobals());

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const file = () => new File(["x"], "deck.pptx");

describe("realKbApi.uploadDocument (#325)", () => {
  it("returns the document ids on success", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ document_ids: ["c/u/deck.pptx"], status: "indexing" })));
    const ids = await realKbApi.uploadDocument("c", file());
    expect(ids).toEqual(["c/u/deck.pptx"]);
  });

  it("throws UploadBlockedError carrying the message key on a 422", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          { detail: { check_id: "office_encryption", reason_code: "encrypted_office", message_key: "kb.upload.blocked.unreadable" } },
          422,
        ),
      ),
    );
    await expect(realKbApi.uploadDocument("c", file())).rejects.toMatchObject({
      name: "UploadBlockedError",
      messageKey: "kb.upload.blocked.unreadable",
      checkId: "office_encryption",
    });
  });

  it("still throws a generic error on other non-2xx statuses", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ detail: "boom" }, 500)));
    await expect(realKbApi.uploadDocument("c", file())).rejects.not.toBeInstanceOf(UploadBlockedError);
  });
});

describe("realKbApi.listUploadChecks (#325)", () => {
  it("fetches the browser-runnable hint descriptors", async () => {
    const hints = [
      { id: "office_encryption", extensions: [".pptx"], forbid_magic_hex: ["d0cf11e0a1b11ae1"], message_key: "kb.upload.blocked.unreadable" },
    ];
    const fetchMock = vi.fn(async (_url: string) => jsonResponse(hints));
    vi.stubGlobal("fetch", fetchMock);
    const got = await realKbApi.listUploadChecks();
    expect(got).toEqual(hints);
    expect(fetchMock.mock.calls[0][0]).toContain("/kb/upload-checks");
  });
});
