/**
 * The left rail of a chat-first App (#chat-private): the user's own chats
 * (= items, scoped to mine + shared by the private-by-default backend), newest
 * first, each a link that switches the workspace to that chat without leaving
 * the surface — the opencode-style session list. Manifest-gated (only mounted
 * for `primary_surface: "chat"` Apps), so it stays App-agnostic.
 *
 * The platform-overview menu + "new chat" button live at its top (a later
 * phase); this is the list itself.
 */

import { Link } from "react-router-dom";

import type { AppItem } from "../api/types";
import { useAppItems } from "../hooks/useResources";

export function ChatListRail({
  slug,
  resourceRoute,
  currentId,
}: {
  slug: string;
  resourceRoute: string | undefined;
  currentId: string;
}) {
  const { items, isPending } = useAppItems(slug, resourceRoute);
  return (
    <nav className="chat-rail" aria-label="chats">
      <div className="chat-rail__list">
        {isPending && items.length === 0 ? (
          <div className="chat-rail__empty">Loading…</div>
        ) : items.length === 0 ? (
          <div className="chat-rail__empty">No chats yet</div>
        ) : (
          items.map((it: AppItem) => (
            <Link
              key={it.resource_id}
              to={`/a/${slug}/${encodeURIComponent(it.resource_id)}`}
              className="chat-rail__item"
              data-active={it.resource_id === currentId ? "true" : undefined}
              title={it.title || "Untitled chat"}
            >
              {it.title || "Untitled chat"}
            </Link>
          ))
        )}
      </div>
    </nav>
  );
}
