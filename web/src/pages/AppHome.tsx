/**
 * App home dispatcher (`/a/:slug`) — #chat-private. A chat-first App opens like a
 * private chat app (resume last chat / empty state, via ChatFirstAppHome); every
 * other App keeps the existing item-grid dashboard. Manifest-gated, no slug
 * branching — the `new` child route (create form) renders inside whichever home.
 */

import { useParams } from "react-router-dom";

import { useAppManifest } from "../hooks/useResources";
import { AppDashboard } from "./AppDashboard";
import { ChatFirstAppHome } from "./ChatFirstAppHome";

export function AppHome() {
  const { slug = "" } = useParams();
  const manifest = useAppManifest(slug);
  // Divert to the chat home ONLY once the manifest confirms chat-first; until
  // then (and for every ide/views App) fall through to the dashboard, which
  // owns its own loading skeleton. A chat-first App shows at most a brief
  // skeleton on a cold load (the manifest is cached thereafter), never the full
  // item grid.
  if (manifest?.layout.primary_surface === "chat") {
    return <ChatFirstAppHome slug={slug} manifest={manifest} />;
  }
  return <AppDashboard />;
}
