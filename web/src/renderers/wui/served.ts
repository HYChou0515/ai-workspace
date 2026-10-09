/**
 * Where a page is served from, when it can be (`docs/plan-wui-multipage.md`).
 *
 * A page shown as one assembled `srcdoc` document has `location` =
 * `about:srcdoc`, so a multi-page site's links, fragments and every script that
 * derives a URL from `location` break (Phase 1 measured mkdocs-material dying
 * on its first line). Served from a real address they all just work — the
 * frame loads the folder from the content route, under a pass this pane mints.
 *
 * Whether that address WORKS here is a fact about the deployment, not about
 * the page: a gateway in front of the app that checks a session cookie on
 * every request turns the frame away, because an opaque-origin frame sends no
 * cookie. So it is measured — one cookie-less request, made the way the frame
 * will make its own — and anything but the route's own answer means the page
 * is shown the single-page way, as it always was.
 */

import { API_PREFIX, apiFetch } from "../../api/http";

/** What `__wui/ping` answers (`PING_ANSWER` in `api/wui_content.py`). */
export const PING_ANSWER = "wui-content";

/** How long the probe may take. A gateway that holds a cookie-less request open
 * instead of refusing it would otherwise leave the pane on "Opening…" forever. */
export const PROBE_TIMEOUT_MS = 8000;

/**
 * The address a frame loads this folder from — `<prefix>/wui-content/<pass>/`,
 * to which the page's workspace path (without its leading slash) is appended —
 * or `null` where this deployment cannot serve it and the page must be shown as
 * one document.
 */
export async function openServedWui(slug: string, itemId: string, folder: string): Promise<string | null> {
  try {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/wui/pass`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ folder: folder || "/" }),
      },
    );
    if (!resp.ok) return null;
    const { base } = (await resp.json()) as { base: string };
    const address = `${API_PREFIX}${base}`;
    const stop = new AbortController();
    const timer = setTimeout(() => stop.abort(), PROBE_TIMEOUT_MS);
    try {
      const ping = await fetch(`${address}__wui/ping`, { credentials: "omit", signal: stop.signal });
      if (!ping.ok || (await ping.text()) !== PING_ANSWER) return null;
      return address;
    } finally {
      clearTimeout(timer);
    }
  } catch {
    return null;
  }
}
