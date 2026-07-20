import type { AgentEvent } from "../events";

/**
 * Parse a server's `text/event-stream` body into a typed event generator.
 * Stops when the server closes the connection or `signal` aborts.
 *
 * Tolerates malformed JSON payloads (small models can emit junky tokens;
 * see plan-backend.md "small-model retry-with-feedback").
 */
export async function* parseSseStream<T = AgentEvent>(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<T> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 2);
      if (!chunk.startsWith("data:")) continue;
      const payload = chunk.slice("data:".length).trim();
      if (!payload) continue;
      try {
        yield JSON.parse(payload) as T;
      } catch {
        // Dropping the frame is right — one bad event must not kill the stream —
        // but doing it silently means a server/client schema drift produces
        // "some events just don't arrive" with nothing anywhere to explain it.
        console.warn("sse: dropped a malformed frame", payload.slice(0, 200));
      }
    }
  }
}
