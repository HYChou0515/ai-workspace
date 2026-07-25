/**
 * The home of a chat-first App (`/a/:slug`) — #chat-private. Instead of the old
 * item-grid dashboard, entering a chat-first App resumes THIS App's last chat
 * (or its newest one); with no chats it lands on a clean "start a new chat"
 * empty state beside the (empty) rail — never the grid. The App gallery lives at
 * `/` and behind the rail menu, so nothing is lost.
 */

import { useEffect } from "react";
import { Link, Navigate, Outlet, useMatch } from "react-router-dom";

import type { AppManifest } from "../api/types";
import { ChatListRail } from "../components/ChatListRail";
import { useNavChrome } from "../hooks/useNavChrome";
import { useAppItems } from "../hooks/useResources";
import { recallLastChat } from "../lib/lastChat";

export function ChatFirstAppHome({ slug, manifest }: { slug: string; manifest: AppManifest }) {
  const { items, isPending } = useAppItems(slug, manifest.resource_route);
  // While the create form is open (`/a/:slug/new`) we must NOT redirect away —
  // the form renders in the Outlet below.
  const creating = Boolean(useMatch("/a/:slug/new"));
  // Chat-first surface → hide the platform top bar (rail menu carries it).
  const { setHidden } = useNavChrome();
  useEffect(() => {
    setHidden(true);
    return () => setHidden(false);
  }, [setHidden]);

  // Resume this App's remembered chat if it still exists, else its newest one.
  const remembered = recallLastChat(slug);
  const resumeId =
    remembered && items.some((i) => i.resource_id === remembered) ? remembered : items[0]?.resource_id;

  if (!isPending && !creating && resumeId) {
    return <Navigate to={`/a/${slug}/${encodeURIComponent(resumeId)}`} replace />;
  }

  return (
    <div className="chat-workspace">
      <ChatListRail slug={slug} resourceRoute={manifest.resource_route} currentId="" />
      <div className="chat-workspace__main chat-empty">
        {!isPending && items.length === 0 && (
          <div className="chat-empty__card">
            <div className="chat-empty__title">No chats yet</div>
            <div className="chat-empty__hint">Start a conversation — it stays private to you.</div>
            <Link className="chat-empty__cta" to={`/a/${slug}/new`}>
              + Start a new chat
            </Link>
          </div>
        )}
        <Outlet />
      </div>
    </div>
  );
}
