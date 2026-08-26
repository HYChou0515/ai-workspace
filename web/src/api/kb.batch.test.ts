/**
 * #730: a card's attachments resolve in ONE request.
 *
 * Against `realKbApi`, not the mock. The first version of these assertions ran
 * on `mockKbApi` — so mutating the shipped `getSourceDocMetas` (dropping the
 * empty-set short circuit, keying the result by position instead of by id)
 * changed nothing they could observe, and both mutations came back green. A
 * test of the double is a test of the double.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { realKbApi } from "./kb";

afterEach(() => vi.unstubAllGlobals());

function envelope(id: string, contentType: string, fileId: string) {
  return {
    data: { content: { file_id: fileId, content_type: contentType } },
    revision_info: { resource_id: id },
  };
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("realKbApi.getSourceDocMetas (#730)", () => {
  it("asks once for every id, and keys the answer by id", async () => {
    const a = "c\u2215a.png";
    const b = "c\u2215b.pdf";
    const fetchMock = vi.fn(async () =>
      // Deliberately the REVERSE of the requested order: the backend is free to
      // return rows however it likes, and a positional mapping would put one
      // card's picture on another's tile — wrong in a way that looks right.
      jsonResponse([envelope(b, "application/pdf", "hb"), envelope(a, "image/png", "ha")]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const metas = await realKbApi.getSourceDocMetas([a, b]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(metas[a]?.content_type).toBe("image/png");
    expect(metas[a]?.file_id).toBe("ha");
    expect(metas[b]?.content_type).toBe("application/pdf");
  });

  it("sends every id in the query", async () => {
    const seen: string[] = [];
    const fetchMock = vi.fn(async (input: unknown) => {
      seen.push(String(input));
      return jsonResponse([]);
    });
    vi.stubGlobal("fetch", fetchMock);

    await realKbApi.getSourceDocMetas(["c\u2215a.png", "c\u2215b.pdf"]);

    const url = seen[0] ?? "";
    expect(decodeURIComponent(url)).toContain("QB.resource_id().in_(");
    expect(decodeURIComponent(url)).toContain("a.png");
    expect(decodeURIComponent(url)).toContain("b.pdf");
  });

  it("asks nothing when there is nothing to ask about", async () => {
    // `in_([])` is not "no query" — it is a query the backend still answers,
    // and a card with no attachments is the common case.
    const fetchMock = vi.fn(async () => jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    expect(await realKbApi.getSourceDocMetas([])).toEqual({});

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
