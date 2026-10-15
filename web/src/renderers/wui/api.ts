/**
 * The one call a WUI makes that is not a file operation.
 *
 * Kept out of `bridge.ts` so the gate stays a pure decision about what is
 * allowed, testable without a network — and out of the shared `api` surface
 * because nothing but a WUI has any business running a tool from a browser.
 */

import { apiFetch, detailSentence } from "../../api/http";
import type { CallAgent, CallTool, JobState, PageJobs } from "./bridge";

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
      const detail = await detailSentence(resp);
      throw new Error(detail ?? `${name} could not be run (${resp.status}).`);
    }
    return resp.json();
  };
}

/** One JSON request to an item's WUI route, raising the server's own sentence. */
async function wuiJson<T>(
  slug: string,
  itemId: string,
  tail: string,
  fallback: string,
  body?: unknown,
): Promise<T> {
  const resp = await apiFetch(
    `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/wui/${tail}`,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!resp.ok) {
    const detail = await detailSentence(resp);
    throw new Error(detail ?? `${fallback} (${resp.status}).`);
  }
  return resp.json();
}

/** Bind `callAgent` (#942) to one item: the sub-agent route, shaped as the
 * tool route is — `{input}` in, `{output, failed}` back. */
export function itemCallAgent(slug: string, itemId: string): CallAgent {
  return (name, input) =>
    wuiJson(slug, itemId, `agents/${encodeURIComponent(name)}/call`, `${name} could not be asked`, {
      input,
    });
}

/** Bind the job routes (#942 `startTool` / `startAgent`) to one item. */
export function itemJobs(slug: string, itemId: string): PageJobs {
  return {
    start: (kind, name, input) =>
      wuiJson(slug, itemId, "jobs", `${name} could not be started`, { kind, name, input }),
    status: (jobId) =>
      wuiJson<JobState>(slug, itemId, `jobs/${encodeURIComponent(jobId)}`, "The job could not be read"),
    stop: (jobId) =>
      wuiJson<JobState>(
        slug,
        itemId,
        `jobs/${encodeURIComponent(jobId)}/stop`,
        "The job could not be stopped",
        {},
      ),
  };
}
