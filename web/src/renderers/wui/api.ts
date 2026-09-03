/**
 * The one call a WUI makes that is not a file operation.
 *
 * Kept out of `bridge.ts` so the gate stays a pure decision about what is
 * allowed, testable without a network — and out of the shared `api` surface
 * because nothing but a WUI has any business running a tool from a browser.
 */

import { apiFetch } from "../../api/http";
import type { CallTool } from "./bridge";

export type ToolResult = { output: string; exit_code: number };

/** Bind the tool-call route to one item. */
export function itemCallTool(slug: string, itemId: string): CallTool {
  return async (name: string, args: Record<string, unknown>): Promise<ToolResult> => {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/wui/tools/${encodeURIComponent(name)}/call`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ args }),
      },
    );
    if (!resp.ok) {
      // The server's own sentence where there is one — it names the tool and
      // why it was refused, which is what the page's error panel shows and what
      // gets forwarded to the agent.
      // FastAPI's `detail` is a STRING for our own refusals and an ARRAY for a
      // validation failure. Passing the array through gave the page — and the
      // report it forwards — "Error: [object Object]", which is the one thing
      // this branch exists to prevent.
      const detail = await resp
        .json()
        .then((b: { detail?: unknown }) => (typeof b.detail === "string" ? b.detail : undefined))
        .catch(() => undefined);
      throw new Error(detail ?? `${name} could not be run (${resp.status}).`);
    }
    return resp.json();
  };
}
