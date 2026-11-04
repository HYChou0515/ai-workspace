/**
 * The one call a WUI makes that is not a file operation.
 *
 * Kept out of `bridge.ts` so the gate stays a pure decision about what is
 * allowed, testable without a network — and out of the shared `api` surface
 * because nothing but a WUI has any business running a tool from a browser.
 */

import { apiFetch, HttpError, refusalFrom } from "../../api/http";
import type { CallAgent, CallTool, JobState, PageJobs } from "./bridge";

export type ToolResult = { output: string; exit_code: number };

/** The refusal code of a page route for want of the WUI User permission
 * (`NEEDS_USE_WUI` in `api/item_authz.py`, docs/plan-wui-user.md D7). */
export const NEEDS_USE_WUI = "needs_use_wui";

/**
 * A page route's failure as an `HttpError` carrying the server's sentence AND
 * its code: the sentence is what the page's error panel shows, the code is what
 * lets the pane say "this page needs the WUI User permission" (D7). Both come
 * from one bounded read of the body (`refusalFrom`).
 */
export async function pageRefusal(resp: Response, fallback: string): Promise<HttpError> {
  const err = await refusalFrom(resp, fallback); // one bounded read of the body
  if (err.code === NEEDS_USE_WUI && err.message === fallback) {
    return new HttpError(err.status, "This page needs the WUI User permission.", err.code, err.also, err.detail);
  }
  return err;
}

/** docs/plan-wui-usage.md U7: the header a page opened by its DEPLOYED link
 * puts on every use, naming the page (its view file's workspace path,
 * percent-encoded — a header value must be Latin-1, and a path can hold any
 * character). The server counts a use only with it, and only for a page that
 * is Deployed. The same page opened inside the item sends nothing. */
export const DEPLOYED_HEADER = "X-Wui-Deployed";

/** The request headers of a use, for a page opened by its deployed link
 * (`deployed` = its view path) or not (undefined). */
export function useHeaders(deployed: string | undefined): Record<string, string> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (deployed) headers[DEPLOYED_HEADER] = encodeURIComponent(deployed);
  return headers;
}

/** Tell the server a deployed page's link was opened (U2). Never throws: the
 * page opens whether or not it could be counted. */
export async function reportOpened(slug: string, itemId: string, path: string): Promise<void> {
  try {
    await apiFetch(`/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/wui/opened`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ path }),
    });
  } catch {
    // Not counted — the reader loses nothing.
  }
}

/** Bind the tool-call route to one item; `deployed` names the page when it was
 * opened by its deployed link (U7). */
export function itemCallTool(slug: string, itemId: string, deployed?: string): CallTool {
  return async (name: string, args: Record<string, unknown>): Promise<ToolResult> => {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/wui/tools/${encodeURIComponent(name)}/call`,
      {
        method: "POST",
        headers: useHeaders(deployed),
        body: JSON.stringify({ args }),
      },
    );
    if (!resp.ok) {
      // The server's own sentence where there is one — it names the tool and
      // why it was refused, which is what the page's error panel shows and what
      // gets forwarded to the agent.
      throw await pageRefusal(resp, `${name} could not be run (${resp.status}).`);
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
  deployed?: string,
): Promise<T> {
  const resp = await apiFetch(
    `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/wui/${tail}`,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: useHeaders(deployed),
          body: JSON.stringify(body),
        },
  );
  if (!resp.ok) {
    throw await pageRefusal(resp, `${fallback} (${resp.status}).`);
  }
  return resp.json();
}

/** Bind `callAgent` (#942) to one item: the sub-agent route, shaped as the
 * tool route is — `{input}` in, `{output, failed}` back. */
export function itemCallAgent(slug: string, itemId: string, deployed?: string): CallAgent {
  return (name, input) =>
    wuiJson(
      slug,
      itemId,
      `agents/${encodeURIComponent(name)}/call`,
      `${name} could not be asked`,
      { input },
      deployed,
    );
}

/** Bind the job routes (#942 `startTool` / `startAgent`) to one item. */
export function itemJobs(slug: string, itemId: string, deployed?: string): PageJobs {
  return {
    // Only starting is a use (U4); asking how it goes, or stopping it, is not.
    start: (kind, name, input) =>
      wuiJson(slug, itemId, "jobs", `${name} could not be started`, { kind, name, input }, deployed),
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
