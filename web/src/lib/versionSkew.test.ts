// @vitest-environment happy-dom
/** Version-skew forced refresh: a cached old bundle must not keep talking to a
 * newer api (the v2026.07.23 incident — old FE × new event shape broke chats). */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  _resetForTest,
  _setReloadImpl,
  checkVersionHeader,
  noteStreamEnd,
  noteStreamStart,
} from "./versionSkew";

const resp = (version: string | null): Response => {
  const headers = new Headers();
  if (version !== null) headers.set("X-App-Version", version);
  return new Response("", { headers });
};

describe("versionSkew", () => {
  const reload = vi.fn();
  beforeEach(() => {
    _resetForTest();
    _setReloadImpl(reload);
    reload.mockClear();
  });
  afterEach(() => _resetForTest());

  it("reloads immediately on a version mismatch when nothing is streaming", () => {
    checkVersionHeader(resp("9999.1.1"));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("does nothing when versions match", () => {
    checkVersionHeader(resp(__APP_VERSION__));
    expect(reload).not.toHaveBeenCalled();
  });

  it("does nothing when the server sends no version header", () => {
    // (a proxy or an older api) — absence is not evidence of skew.
    checkVersionHeader(resp(null));
    expect(reload).not.toHaveBeenCalled();
  });

  it("defers the reload while a turn is streaming, fires when it ends", () => {
    noteStreamStart();
    checkVersionHeader(resp("9999.1.1"));
    expect(reload).not.toHaveBeenCalled(); // don't cut a live answer

    noteStreamEnd();
    expect(reload).toHaveBeenCalledTimes(1); // safe moment reached
  });

  it("waits for the LAST of several streams", () => {
    noteStreamStart();
    noteStreamStart();
    checkVersionHeader(resp("9999.1.1"));
    noteStreamEnd();
    expect(reload).not.toHaveBeenCalled();
    noteStreamEnd();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("reloads at most once no matter how many mismatches arrive", () => {
    checkVersionHeader(resp("9999.1.1"));
    checkVersionHeader(resp("9999.1.1"));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});

describe("wiring", () => {
  const reload = vi.fn();
  beforeEach(() => {
    _resetForTest();
    _setReloadImpl(reload);
    reload.mockClear();
  });
  afterEach(() => {
    _resetForTest();
    vi.unstubAllGlobals();
  });

  it("apiFetch checks every response for skew", async () => {
    const { apiFetch } = await import("../api/http");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => resp("9999.1.1")),
    );
    await apiFetch("/anything");
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("an open SSE stream defers the reload until it finishes", async () => {
    const { parseSseStream } = await import("../api/sse");
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode('data: {"type":"done"}\n\n'));
        c.close();
      },
    });
    const it = parseSseStream(body)[Symbol.asyncIterator]();
    await it.next(); // stream is live now
    checkVersionHeader(resp("9999.1.1"));
    expect(reload).not.toHaveBeenCalled(); // held while the stream is open
    await it.next(); // exhausted → stream closes
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
