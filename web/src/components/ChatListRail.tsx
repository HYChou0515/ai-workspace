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

import { useState } from "react";
import { Link } from "react-router-dom";

import type { AppItem } from "../api/types";
import { useAppItems, useApps } from "../hooks/useResources";

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
  const [menuOpen, setMenuOpen] = useState(false);
  const closeMenu = () => setMenuOpen(false);

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
        <Link className="chat-rail__new" to={`/a/${slug}/new`}>
          + New chat
        </Link>
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
