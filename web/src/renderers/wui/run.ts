/**
 * Starting a run from a page, and watching it happen.
 *
 * The synchronous half of the same engine a schedule uses. Once a page's
 * judgement can read files and call tools it is not "a question with an answer"
 * — it is work that takes minutes, and work that takes minutes needs a record,
 * a way to stop it, and progress somebody can see. All three already exist for
 * a run, so a click starts one rather than inventing a lighter thing that would
 * end up needing the same three.
 *
 * The events reach the page VERBATIM. Its author decides what to draw with
 * them: the pane's own chrome does not know which row was clicked, and a WUI's
 * whole premise is that the person who wrote it owns the experience.
 */

import { apiFetch, HttpError } from "../../api/http";
import { parseSseStream } from "../../api/sse";

/** Start a run for one item, yielding the platform's events as they arrive. */
export function itemRun(slug: string, itemId: string) {
  return async function* (
    workflow: string,
    payload: Record<string, unknown>,
    signal?: AbortSignal,
  ): AsyncGenerator<unknown> {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/wui/run`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ workflow, with: payload }),
        signal,
      },
    );
    if (!resp.ok || !resp.body) {
      // The server's own sentence where there is one. It names which workflow
      // and why not, and that reaches a person through the page's error panel —
      // "run failed" would send them nowhere. `detail` is a string for our
      // refusals and an array for a validation failure; passing the array
      // through prints "[object Object]" where the explanation should be.
      const detail = await resp
        .json()
        .then((body: { detail?: unknown }) =>
          typeof body?.detail === "string" ? body.detail : null,
        )
        .catch(() => null);
      throw new HttpError(resp.status, detail ?? `${workflow} could not be started (${resp.status}).`);
    }
    for await (const frame of parseSseStream(resp.body)) {
      yield frame;
    }
  };
}
