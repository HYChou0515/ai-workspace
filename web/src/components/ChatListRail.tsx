/**
 * The left rail of a chat-first App (#chat-private): the user's own chats
 * (= items, scoped to mine + shared by the private-by-default backend), newest
 * first, each a link that switches the workspace to that chat without leaving
 * the surface — the opencode-style session list. Manifest-gated (only mounted
 * for `primary_surface: "chat"` Apps), so it stays App-agnostic.
 *
 * Its top carries a "New chat" action and a menu button that tucks the whole
 * platform overview (App switcher + Knowledge base / Review / Diagnostics /
 * Help) behind one press, so the chat surface stays clean.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import type { AppItem } from "../api/types";
import { useChatActions } from "../hooks/useChatActions";
import { useCreateChat } from "../hooks/useCreateChat";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { useIsNarrow } from "../hooks/useMediaQuery";
import { useAppItems, useApps } from "../hooks/useResources";
import { ShareChatDialog } from "./ShareChatDialog";

// Platform destinations that live behind the menu (App-agnostic — the App
// switcher is data-driven from useApps, these are the fixed platform surfaces).
const PLATFORM_LINKS: { to: string; label: string }[] = [
  { to: "/kb", label: "Knowledge base" },
  { to: "/review", label: "Review" },
  { to: "/diagnostics", label: "Diagnostics" },
  { to: "/help", label: "Help" },
];

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
  const apps = useApps();
  const me = useCurrentUser();
  const createChat = useCreateChat(slug);
  const actions = useChatActions(slug, resourceRoute);
  const [menuOpen, setMenuOpen] = useState(false);
  // #chat-private #3: the rail can be collapsed to a thin bar (not persisted —
  // like the IDE state, a new tab opens with it shown).
  // #fe-responsive: 240px of a 390px viewport left 150px for the whole chat
  // surface, so narrow starts it tucked. Tracked symmetrically (both ways
  // across the breakpoint) for the same reason the shell's sidebar is: a
  // one-way rule strands the user on the far side of a resize.
  const isNarrow = useIsNarrow();
  const [collapsed, setCollapsed] = useState(isNarrow);
  useEffect(() => {
    setCollapsed(isNarrow);
  }, [isNarrow]);
  // #chat-private: split my chats from ones shared with me (owner !== me).
  const [tab, setTab] = useState<"mine" | "shared">("mine");
  const mine = items.filter((it) => it.owner === me);
  const shared = items.filter((it) => it.owner !== me);
  const shown = tab === "mine" ? mine : shared;
  const closeMenu = () => setMenuOpen(false);

  if (collapsed) {
    return (
      <nav className="chat-rail chat-rail--collapsed" aria-label="chats">
        <button
          type="button"
          className="chat-rail__expand"
          aria-label="Show chats"
          onClick={() => setCollapsed(false)}
        >
          »
        </button>
      </nav>
    );
  }

  return (
    <nav className="chat-rail" aria-label="chats">
      <div className="chat-rail__head">
        <button
          type="button"
          className="chat-rail__menu-btn"
          aria-label="Platform menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((o) => !o)}
        >
          ☰
        </button>
        <button
          type="button"
          className="chat-rail__new"
          onClick={() => createChat.mutate()}
          disabled={createChat.isPending}
        >
          + New chat
        </button>
        <button
          type="button"
          className="chat-rail__collapse"
          aria-label="Collapse chats"
          onClick={() => setCollapsed(true)}
        >
          «
        </button>
      </div>

      {menuOpen && (
        <>
          <div className="chat-rail__backdrop" onClick={closeMenu} />
          <div className="chat-rail__menu" role="menu">
            <div className="chat-rail__menu-label">Apps</div>
            {apps.map((a) => (
              <Link
                key={a.slug}
                role="menuitem"
                className="chat-rail__menu-item"
                to={`/a/${a.slug}`}
                onClick={closeMenu}
              >
                {a.title}
              </Link>
            ))}
            <div className="chat-rail__menu-sep" />
            {PLATFORM_LINKS.map((l) => (
              <Link
                key={l.to}
                role="menuitem"
                className="chat-rail__menu-item"
                to={l.to}
                onClick={closeMenu}
              >
                {l.label}
              </Link>
            ))}
          </div>
        </>
      )}

      <div className="chat-rail__tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "mine"}
          className="chat-rail__tab"
          data-active={tab === "mine" ? "true" : undefined}
          onClick={() => setTab("mine")}
        >
          My chats
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "shared"}
          className="chat-rail__tab"
          data-active={tab === "shared" ? "true" : undefined}
          onClick={() => setTab("shared")}
        >
          Shared with me
        </button>
      </div>

      <div className="chat-rail__list">
        {isPending && items.length === 0 ? (
          <div className="chat-rail__empty">Loading…</div>
        ) : shown.length === 0 ? (
          <div className="chat-rail__empty">
            {tab === "mine" ? "No chats yet" : "Nothing shared with you yet"}
          </div>
        ) : (
          shown.map((it: AppItem) => (
            <ChatRailItem
              key={it.resource_id}
              slug={slug}
              item={it}
              active={it.resource_id === currentId}
              onRename={actions.rename}
              onDelete={actions.remove}
            />
          ))
        )}
      </div>
    </nav>
  );
}

/** One chat row: a link that switches to it, plus a ⋯ menu to rename (inline) or
 * delete (confirmed). */
function ChatRailItem({
  slug,
  item,
  active,
  onRename,
  onDelete,
}: {
  slug: string;
  item: AppItem;
  active: boolean;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [draft, setDraft] = useState("");
  const me = useCurrentUser();
  const shared = item.owner !== me; // shared WITH me → I don't own it
  const title = item.title || "Untitled chat";

  if (editing) {
    const commit = (value: string) => {
      const next = value.trim();
      if (next && next !== title) onRename(item.resource_id, next);
      setEditing(false);
    };
    return (
      <input
        // eslint-disable-next-line jsx-a11y/no-autofocus
        autoFocus
        className="chat-rail__rename"
        aria-label="Rename chat"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit(draft);
          if (e.key === "Escape") setEditing(false);
        }}
        onBlur={() => setEditing(false)}
      />
    );
  }

  return (
    <div className="chat-rail__row">
      <Link
        to={`/a/${slug}/${encodeURIComponent(item.resource_id)}`}
        className="chat-rail__item"
        data-active={active ? "true" : undefined}
        title={title}
      >
        <span className="chat-rail__item-title">{title}</span>
      </Link>
      {/* Rename / Share / Delete only for chats I own; a shared-with-me chat just
          shows the "shared" tag (the backend would 403 those actions anyway). */}
      {!shared && (
        <>
          <button
            type="button"
            className="chat-rail__more"
            aria-label={`Chat options for ${title}`}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((o) => !o)}
          >
            ⋯
          </button>
          {menuOpen && (
            <>
              <div className="chat-rail__backdrop" onClick={() => setMenuOpen(false)} />
              <div className="chat-rail__rowmenu" role="menu">
                <button
                  type="button"
                  role="menuitem"
                  className="chat-rail__menu-item"
                  onClick={() => {
                    setMenuOpen(false);
                    setDraft(title);
                    setEditing(true);
                  }}
                >
                  Rename
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="chat-rail__menu-item"
                  onClick={() => {
                    setMenuOpen(false);
                    setSharing(true);
                  }}
                >
                  Share
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="chat-rail__menu-item chat-rail__menu-item--danger"
                  onClick={() => {
                    setMenuOpen(false);
                    if (window.confirm(`Delete “${title}”? This can't be undone.`)) {
                      onDelete(item.resource_id);
                    }
                  }}
                >
                  Delete
                </button>
              </div>
            </>
          )}
        </>
      )}
      {sharing && <ShareChatDialog slug={slug} item={item} onClose={() => setSharing(false)} />}
    </div>
  );
}
