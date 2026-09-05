/**
 * What crosses the frame boundary.
 *
 * The iframe has a null origin, so `postMessage` is the ONLY channel between a
 * WUI and the platform — which makes this file the whole API surface, and the
 * reason it is kept small: every verb here is code that has to be trusted.
 *
 * Messages carry `proto` because a page shares its window with nothing else we
 * control: anything without the tag is somebody else's message and is ignored.
 *
 * **The verb set is closed to CAPABILITIES.** Anything that reaches an outside
 * system arrives as another `callTool` target — never as another verb. What may
 * still be added is a platform PRIMITIVE, something in the same class as reading
 * a file, and only when it cannot be expressed as a tool call.
 *
 * (This replaces a flat "never another verb, ever". That rule was written before
 * anything needed a primitive, and it was not the rule that was kept — so it is
 * stated once, here, in the form it actually has. Two versions of a rule in one
 * file means the reader gets whichever they stop at.)
 *
 * `startRun` (#WUI P18) is the one such addition so far, and the reason is
 * mechanical rather than a matter of taste: `callTool` answers exactly once, and
 * a run reports progress for minutes before it answers. Every future addition
 * owes the same argument in writing, or this rule has quietly become nothing.
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
  | { proto: typeof WUI_PROTOCOL; id: string; ok: false; error: string; expected?: true };

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

/**
 * A "no" that is part of ordinary use, not a fault.
 *
 * A page's first run reads a data file that does not exist yet — the reference
 * documents that as the way to start empty — so reporting it like a refusal put
 * a red "this page was not allowed to do that" in front of every user opening
 * every new WUI. An alarm that always fires is one nobody reads, which costs
 * exactly the refusals that DO matter. It still rejects: the page's `.catch`
 * is unchanged.
 */
export function refuseExpected(id: string, error: string): WuiResponse {
  return { proto: WUI_PROTOCOL, id, ok: false, error, expected: true };
}
