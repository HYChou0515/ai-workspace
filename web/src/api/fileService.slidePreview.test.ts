/** How the item file service reads the preview route's three answers
 * (docs/plan-pptx-preview.md D3, D5): a PDF, "big — ask first", a reason. */
// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";

import { investigationFileService } from "./fileService";

afterEach(() => vi.unstubAllGlobals());

function answer(status: number, body: BodyInit, type = "application/json") {
  const seen: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      seen.push(String(url));
      // Only the preview route answers; anything else is a test that went astray.
      if (!String(url).includes("/files/preview?")) return new Response("", { status: 599 });
      return new Response(body, { status, headers: { "content-type": type } });
    }),
  );
  return seen;
}

describe("slidePreview", () => {
  it("asks the item's preview route, passing the person's yes", async () => {
    const seen = answer(200, "%PDF", "application/pdf");

    await investigationFileService("rca", "i 1").slidePreview!("/slides/q3 deck.pptx", true);

    expect(seen[0]).toContain("/a/rca/items/i%201/files/preview?path=%2Fslides%2Fq3%20deck.pptx&confirm=1");
  });

  it("hands back the PDF", async () => {
    answer(200, "%PDF", "application/pdf");

    const got = await investigationFileService("rca", "i1").slidePreview!("/q3.pptx", false);

    expect(got.kind).toBe("pdf");
  });

  it("reads 409 as 'ask first', with the server's numbers", async () => {
    answer(409, JSON.stringify({ detail: { code: "preview_needs_confirm", size: 42, limit: 20 } }));

    const got = await investigationFileService("rca", "i1").slidePreview!("/q3.pptx", false);

    expect(got).toEqual({ kind: "confirm", size: 42, limit: 20 });
  });

  it("reads 422 as the converter's reason", async () => {
    answer(422, JSON.stringify({ detail: { code: "preview_failed", why: "could not be loaded" } }));

    const got = await investigationFileService("rca", "i1").slidePreview!("/q3.pptx", false);

    expect(got).toEqual({ kind: "failed", why: "could not be loaded" });
  });

  it("throws on anything else, so the page shows it rather than a blank", async () => {
    answer(404, JSON.stringify({ detail: "/q3.pptx not found" }));

    await expect(
      investigationFileService("rca", "i1").slidePreview!("/q3.pptx", false),
    ).rejects.toThrow(/404/);
  });
});
