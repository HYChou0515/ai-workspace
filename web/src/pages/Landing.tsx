/**
 * Landing (`/`) — the private-chat front door. If the user has a last-opened
 * chat, resume it (opencode-style "continue where you left off"); otherwise
 * fall back to the App gallery to pick one and start. The App gallery itself
 * moves behind the rail's platform menu once you're in a chat.
 *
 * The last chat is remembered per-device in localStorage (written by the item
 * workspace on open) — simplest, no backend round-trip; a stale/removed item
 * just lands the user on its workspace's own not-found state.
 */

import { Navigate } from "react-router-dom";

import { Launcher } from "./Launcher";

export const LAST_CHAT_KEY = "chat:last";

export function Landing() {
  let last: string | null = null;
  try {
    last = localStorage.getItem(LAST_CHAT_KEY);
  } catch {
    /* privacy mode / disabled storage → just show the gallery */
  }
  if (last && last.startsWith("/a/")) {
    return <Navigate to={last} replace />;
  }
  return <Launcher />;
}
