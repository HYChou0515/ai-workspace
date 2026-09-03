import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "../../api/http";
import { cleanBuildOutput, hasBuildScript, itemBuild } from "./build";

afterEach(() => vi.unstubAllGlobals());

/** A response whose body arrives in pieces, with a gate between them — the
 * shape a real build has, and the only shape that can tell a streaming reader
 * apart from one that waits for the end. */
function streaming(chunks: string[]) {
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => (release = r));
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      const encode = new TextEncoder();
      controller.enqueue(encode.encode(chunks[0]));
      await gate;
      for (const chunk of chunks.slice(1)) controller.enqueue(encode.encode(chunk));
      controller.close();
    },
  });
  const resp = new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
  vi.stubGlobal("fetch", vi.fn(async () => resp));
  return { release };
}

function frame(payload: unknown) {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

describe("itemBuild", () => {
  it("hands over each line while the build is still running", async () => {
    // The whole point of the feature. A build takes tens of seconds; a client
    // that collected the output and revealed it at the end would leave a person
    // watching a spinner, unable to tell a slow build from a hung one.
    const { release } = streaming([
      frame({ type: "output", text: "> vite build\n" }),
      frame({ type: "output", text: "built in 615ms\n" }),
      frame({ type: "done", exit_code: 0 }),
    ]);

    const seen: string[] = [];
    const stream = itemBuild("rca", "i1")("/page");

    const first = await stream.next();
    expect(first.value).toEqual({ type: "output", text: "> vite build\n" });
    seen.push("first arrived before the rest was sent");

    release();
    for await (const event of stream) seen.push(JSON.stringify(event));
    expect(seen).toEqual([
      "first arrived before the rest was sent",
      JSON.stringify({ type: "output", text: "built in 615ms\n" }),
      JSON.stringify({ type: "done", exit_code: 0 }),
    ]);
  });

  it("posts the page's folder to the item's own route", async () => {
    const { release } = streaming([frame({ type: "done", exit_code: 0 })]);
    release();

    const stream = itemBuild("rca", "i1")("/page");
    for await (const _ of stream) void _;

    const spy = vi.mocked(fetch);
    const [url, init] = spy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/a/rca/items/i1/wui/build");
    expect(JSON.parse(String(init.body))).toEqual({ folder: "/page" });
  });

  it("raises the server's own sentence when the build is refused", async () => {
    // A refusal reaches a person through the same log the build's output uses,
    // so replacing it with a status code loses the only actionable part.
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "/etc is not a page folder." }), { status: 400 }),
      ),
    );

    const stream = itemBuild("rca", "i1")("/etc");
    await expect(stream.next()).rejects.toThrow(/not a page folder/);
  });

  it("carries the status, because the pane acts differently on a 403", async () => {
    // A viewer who may read the item but not run things in it is refused
    // permanently, and the pane stops rebuilding on open for them rather than
    // greeting them with the same refusal every time. That decision needs the
    // status, so a bare `Error` would erase it.
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "You cannot run things here." }), { status: 403 }),
      ),
    );

    await itemBuild("rca", "i1")("/page")
      .next()
      .then(
        () => expect.unreachable("the refusal must reach the caller"),
        (err: unknown) => {
          expect(err).toBeInstanceOf(HttpError);
          expect((err as HttpError).status).toBe(403);
        },
      );
  });

  it("still says something when the failure carries no sentence", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>gateway</html>", { status: 502 })));

    const stream = itemBuild("rca", "i1")("/page");
    await expect(stream.next()).rejects.toThrow(/502/);
  });
});

describe("hasBuildScript", () => {
  it("is true only when the page declares the script the platform runs", () => {
    // `pnpm run build` is what the route runs, so `scripts.build` is exactly the
    // question — not "is there a package.json", which a page can have for other
    // reasons and which would put a button in front of a build that cannot run.
    expect(hasBuildScript(JSON.stringify({ scripts: { build: "vite build" } }))).toBe(true);
    expect(hasBuildScript(JSON.stringify({ scripts: { test: "vitest" } }))).toBe(false);
    expect(hasBuildScript(JSON.stringify({ name: "page" }))).toBe(false);
  });

  it("says no rather than throwing on a file that is not a package manifest", () => {
    // Anyone may put anything in the folder. A parse error here would take the
    // whole pane down, over a file that has nothing to do with the page.
    expect(hasBuildScript("{ not json")).toBe(false);
    expect(hasBuildScript("")).toBe(false);
    expect(hasBuildScript("[]")).toBe(false);
    expect(hasBuildScript(JSON.stringify({ scripts: "build" }))).toBe(false);
    expect(hasBuildScript(JSON.stringify({ scripts: { build: "" } }))).toBe(false);
  });
});

describe("cleanBuildOutput", () => {
  it("shows the build's words, not its colour codes", () => {
    // Every real build tool colours its output, and a browser renders none of
    // it: the pane showed `[32m✓[39m built in 565ms` — the escape sequences as
    // literal text, which reads as a broken tool rather than a working one.
    // Seen in a screenshot of the first real rebuild; no unit test could have.
    const raw = "\u001b[2mdist/\u001b[22m\u001b[32mindex.html\u001b[39m 0.60 kB\n";

    expect(cleanBuildOutput(raw)).toBe("dist/index.html 0.60 kB\n");
  });

  it("keeps text that merely looks like an escape", () => {
    // A compiler error quoting source is the reason to read this log at all.
    expect(cleanBuildOutput("expected [32m] to be a number")).toBe(
      "expected [32m] to be a number",
    );
  });

  it("turns a progress rewrite into a line rather than one long smear", () => {
    // `\r` redraws a line in a terminal and does nothing in a <div>, so a
    // progress bar arrived as one unreadable run-on line.
    expect(cleanBuildOutput("Progress: 1\rProgress: 2\r\n")).toBe("Progress: 1\nProgress: 2\n");
  });

  it("leaves ordinary output exactly alone", () => {
    expect(cleanBuildOutput("> vite build\nbuilt in 615ms\n")).toBe(
      "> vite build\nbuilt in 615ms\n",
    );
  });
});
