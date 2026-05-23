import { describe, expect, it } from "vitest";
import type { AgentEvent } from "../events";
import { parseSseStream } from "./sse";

/** Build a ReadableStream that emits each chunk (string) verbatim, then closes. */
function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(chunks[i++]));
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<AgentEvent[]> {
  const out: AgentEvent[] = [];
  for await (const ev of parseSseStream(stream)) out.push(ev);
  return out;
}

describe("parseSseStream", () => {
  it("yields a single event delivered in one chunk", async () => {
    const events = await collect(
      streamOf([`data: ${JSON.stringify({ type: "done" })}\n\n`]),
    );
    expect(events).toEqual([{ type: "done" }]);
  });

  it("yields two events delivered in one chunk", async () => {
    const events = await collect(
      streamOf([
        `data: ${JSON.stringify({ type: "message_delta", text: "hi" })}\n\n` +
          `data: ${JSON.stringify({ type: "done" })}\n\n`,
      ]),
    );
    expect(events).toEqual([
      { type: "message_delta", text: "hi" },
      { type: "done" },
    ]);
  });

  it("reassembles an event split across chunks", async () => {
    const payload = JSON.stringify({ type: "message_delta", text: "split" });
    const head = `data: ${payload.slice(0, 8)}`;
    const tail = `${payload.slice(8)}\n\n`;
    const events = await collect(streamOf([head, tail]));
    expect(events).toEqual([{ type: "message_delta", text: "split" }]);
  });

  it("swallows malformed JSON and continues with the next event", async () => {
    const events = await collect(
      streamOf([
        `data: {"type": "message_delta", "text": "broken\n\n`,
        `data: ${JSON.stringify({ type: "done" })}\n\n`,
      ]),
    );
    expect(events).toEqual([{ type: "done" }]);
  });

  it("ignores non-data lines and empty payloads", async () => {
    const events = await collect(
      streamOf([
        `event: ping\n\n`,
        `data:\n\n`,
        `data: ${JSON.stringify({ type: "done" })}\n\n`,
      ]),
    );
    expect(events).toEqual([{ type: "done" }]);
  });

  it("drops a trailing partial event when the stream closes mid-frame", async () => {
    // No blank-line terminator → not a complete SSE frame; parser must not
    // emit anything for it.
    const events = await collect(
      streamOf([`data: ${JSON.stringify({ type: "done" })}`]),
    );
    expect(events).toEqual([]);
  });
});
