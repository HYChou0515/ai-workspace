/**
 * What crosses the frame boundary.
 *
 * The iframe has a null origin, so `postMessage` is the ONLY channel between a
 * WUI and the platform — which makes this file the whole API surface. It is
 * deliberately small and deliberately CLOSED: every future capability arrives
 * as another `callTool` target, never as another verb, so the amount of code
 * that has to be trusted here stays the size it is today.
 *
 * Messages carry `proto` because a page shares its window with nothing else we
 * control: anything without the tag is somebody else's message and is ignored.
 */

export const WUI_PROTOCOL = "wui/1";

/** Frame → parent. `id` is echoed so a page can have several calls in flight. */
export type WuiRequest = {
  proto: typeof WUI_PROTOCOL;
  id: string;
  verb: string;
  args?: Record<string, unknown>;
};

/** Parent → frame, answering one request. A refusal carries a SENTENCE, not a
 * code: it is shown to someone who cannot open a console and is forwarded to
 * the agent verbatim, so it has to say what happened and why. */
export type WuiResponse =
  | { proto: typeof WUI_PROTOCOL; id: string; ok: true; value: unknown }
  | { proto: typeof WUI_PROTOCOL; id: string; ok: false; error: string };

/** Parent → frame, unsolicited. `file_changed` is forwarded rather than acted
 * on: the platform cannot know whether a half-filled form should be discarded,
 * and a page that is an editor must at least be TOLD, or it will overwrite
 * someone else's edit without either of them noticing. */
export type WuiEvent = {
  proto: typeof WUI_PROTOCOL;
  event: "file_changed";
  path: string;
};

export function isWuiRequest(data: unknown): data is WuiRequest {
  if (!data || typeof data !== "object") return false;
  const m = data as Record<string, unknown>;
  return m.proto === WUI_PROTOCOL && typeof m.id === "string" && typeof m.verb === "string";
}

export function ok(id: string, value: unknown): WuiResponse {
  return { proto: WUI_PROTOCOL, id, ok: true, value };
}

export function refuse(id: string, error: string): WuiResponse {
  return { proto: WUI_PROTOCOL, id, ok: false, error };
}
