import { afterEach, describe, expect, it, vi } from "vitest";

import {
  cancelChatVideo,
  fetchChatTranscript,
  fetchChatVideoLimits,
  startChatVideo,
} from "./chatVideo";
import { fetchChatExport } from "./workflows";

afterEach(() => vi.unstubAllGlobals());

function json(body: unknown, status = 200, type = "application/json"): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": type } });
}

describe("fetchChatExport with a format and a range (P2's route, same call)", () => {
  it("asks for markdown and the half-open range, and accepts text/markdown back", async () => {
    const fetchMock = vi.fn(
      async (_url: string) =>
        new Response("# t", {
          status: 200,
          headers: {
            "content-type": "text/markdown; charset=utf-8",
            "content-disposition": 'attachment; filename="t (8-10).chat.md"',
          },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { filename } = await fetchChatExport("rca", "rca:1", "conversation:c1", {
      format: "md",
      range: { start: 7, end: 10 },
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/export-chat?");
    expect(url).toContain("format=md");
    expect(url).toContain("start=7");
    expect(url).toContain("end=10");
    expect(filename).toBe("t (8-10).chat.md");
  });

  it("sends no range parameters for the whole thread", async () => {
    const fetchMock = vi.fn(async (_url: string) => json({ title: "t", messages: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchChatExport("rca", "rca:1", "conversation:c1", { format: "json", range: null });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("format=json");
    expect(url).not.toContain("start=");
    expect(url).not.toContain("end=");
  });
});

describe("fetchChatTranscript — the export, parsed, for the dialog's range picker", () => {
  it("returns the title and the messages in server order", async () => {
    const doc = { title: "OOM", messages: [{ role: "user", content: "a" }] };
    vi.stubGlobal("fetch", vi.fn(async () => json(doc)));

    expect(await fetchChatTranscript("rca", "rca:1", "conversation:c1")).toEqual(doc);
  });
});

describe("startChatVideo — POST …/items/{id}/chat-video", () => {
  it("posts the transcript, the options and the output path; returns the three paths", async () => {
    const answer = {
      output_path: "/exports/chat-video/OOM-1.mp4",
      source_path: "/exports/chat-video/OOM-1.mp4.chat.json",
      progress_path: "/exports/chat-video/OOM-1.mp4.progress.json",
      expected_seconds: 41,
      stale_after_seconds: 60,
      token: "job-1",
    };
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => json(answer, 202));
    vi.stubGlobal("fetch", fetchMock);
    const transcript = { title: "OOM", messages: [{ role: "user", content: "a" }] };

    const got = await startChatVideo("rca", "rca:1", {
      transcript,
      options: { width: 1920, height: 1080, fmt: ["mp4"] },
      output_path: null,
    });

    expect(got).toEqual(answer);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/a/rca/items/rca%3A1/chat-video");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      transcript,
      options: { width: 1920, height: 1080, fmt: ["mp4"] },
      output_path: null,
    });
  });

  it("throws the server's own sentence on a refusal, not the status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: "a video is already being made at /x.mp4" }, 409)),
    );

    await expect(
      startChatVideo("rca", "rca:1", { transcript: { title: "t", messages: [] }, options: {} }),
    ).rejects.toThrow("a video is already being made at /x.mp4");
  });
});

describe("cancelChatVideo — DELETE …/chat-video?path=", () => {
  it("deletes through the route, and a 404 (already gone) is not an error", async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init?: RequestInit) => new Response(null, { status: 204 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await cancelChatVideo("rca", "rca:1", "/exports/chat-video/OOM-1.mp4.progress.json");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/a/rca/items/rca%3A1/chat-video?path=");
    expect(url).toContain(encodeURIComponent("/exports/chat-video/OOM-1.mp4.progress.json"));
    expect(init?.method).toBe("DELETE");

    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "no such progress file" }, 404)));
    await expect(cancelChatVideo("rca", "rca:1", "/x.progress.json")).resolves.toBeUndefined();
  });

  it("surfaces a refusal as the server's sentence", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "forbidden: edit_content" }, 403)));

    await expect(cancelChatVideo("rca", "rca:1", "/x.progress.json")).rejects.toThrow(
      "forbidden: edit_content",
    );
  });
});

describe("fetchChatVideoLimits — what this deployment allows, for the form", () => {
  it("reads the three ceilings", async () => {
    const limits = { max_pixels: 2073600, max_seconds: 180, max_output_bytes: 100000000 };
    const fetchMock = vi.fn(async (_url: string) => json(limits));
    vi.stubGlobal("fetch", fetchMock);

    expect(await fetchChatVideoLimits("rca", "rca:1")).toEqual(limits);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/a/rca/items/rca%3A1/chat-video");
  });
});
