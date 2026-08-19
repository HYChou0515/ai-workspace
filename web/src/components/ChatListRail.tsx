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
import { useMinWidth } from "../hooks/useMediaQuery";
import { useAppItems, useAppManifest, useApps } from "../hooks/useResources";
import { usePlatformDestinations } from "../hooks/usePlatformDestinations";
import { itemNouns } from "../lib/itemNoun";
import { BREAKPOINTS } from "../lib/breakpoints";
import { ShareChatDialog } from "./ShareChatDialog";
import { UserChip } from "./UserChip";

type Tab = "mine" | "shared";

/** The tab the chat you're in belongs to — "Shared with me" iff it's one of
 * theirs. With no chat open (the App home) that's "My chats". */
function defaultTab(shared: AppItem[], currentId: string): Tab {
  return shared.some((it) => it.resource_id === currentId) ? "shared" : "mine";
}

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
  // The rail lists ITEMS, so it uses the App's word for one. Hardcoding
  // "chat" named the wrong level wherever an item holds many conversations.
  const nouns = itemNouns(useAppManifest(slug));
  // Shared with GlobalNav's switcher — the rail used to keep its own copy and
  // silently lacked My resources, Groups and Work calendar.
  const destinations = usePlatformDestinations();
  const apps = useApps();
  const me = useCurrentUser();
  const createChat = useCreateChat(slug);
  const actions = useChatActions(slug, resourceRoute);
  const [menuOpen, setMenuOpen] = useState(false);
  // #chat-private #3: the rail can be collapsed to a thin bar (not persisted —
  // like the IDE state, a new tab opens with it shown).
  //
  // #fe-responsive: the rail is not a passenger, it sits BESIDE the workspace
  // shell and takes 240px off it. 240 of a 390px viewport left 150px for the
  // whole chat surface; and between `shell` and `shell + rail` the OPEN rail is
  // the very thing that pushes the shell into its single-column layout, which
  // reads as "the app lost its file tree because a chat list is open". So the
  // rail tucks itself first, and only above `shell + rail` does it stay open.
  //
  // Tracked both ways across that threshold, like the shell's own sidebar, so a
  // resize doesn't strand it — the difference from the sidebar is that the 40px
  // `»` bar is always on screen, so this resets a preference rather than
  // rescuing an unreachable panel.
  const railFits = useMinWidth(BREAKPOINTS.shell + BREAKPOINTS.chatRail);
  const [collapsed, setCollapsed] = useState(!railFits);
  useEffect(() => {
    setCollapsed(!railFits);
  }, [railFits]);
  // #chat-private: split my chats from ones shared with me (owner !== me).
  const mine = items.filter((it) => it.owner === me);
  const shared = items.filter((it) => it.owner !== me);
  // Which tab is open FOLLOWS the chat you are in; a click only overrides it for
  // as long as you stay in that chat. It cannot be plain `useState("mine")`:
  // this rail is destroyed on every navigation — `AppWorkspaceInner` renders
  // "Loading…" in place of the whole workspace until the item query answers,
  // which is every first visit to a chat — so a seeded tab meant opening
  // something from "Shared with me" dropped you back on "My chats", with the
  // chat you were now reading absent from the list you were looking at. Derived,
  // it survives that remount, and a reload, without persisting anything.
  const [picked, setPicked] = useState<{ chat: string; tab: Tab } | null>(null);
  const tab = picked?.chat === currentId ? picked.tab : defaultTab(shared, currentId);
  const setTab = (next: Tab) => setPicked({ chat: currentId, tab: next });
  const shown = tab === "mine" ? mine : shared;
  const closeMenu = () => setMenuOpen(false);

  if (collapsed) {
    return (
      <nav className="chat-rail chat-rail--collapsed" aria-label={nouns.plural.toLowerCase()}>
        <button
          type="button"
          className="chat-rail__expand"
          aria-label={`Show ${nouns.plural}`}
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
          + {nouns.createLabel}
        </button>
        <button
          type="button"
          className="chat-rail__collapse"
          aria-label={`Collapse ${nouns.plural}`}
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
            {destinations.map((l) => (
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
          My {nouns.plural}
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
            {tab === "mine" ? `No ${nouns.plural} yet` : "Nothing shared with you yet"}
          </div>
        ) : (
          shown.map((it: AppItem) => (
            <ChatRailItem
              key={it.resource_id}
              slug={slug}
              item={it}
              active={it.resource_id === currentId}
              noun={nouns.noun}
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
  noun,
  onRename,
  onDelete,
}: {
  slug: string;
  item: AppItem;
  active: boolean;
  /** What this App calls one item ("Project"), for the row's own labels. */
  noun: string;
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
        aria-label={`Rename ${noun}`}
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
        {/* Whose chat this is. A shared row is otherwise indistinguishable from
            one of my own once it's in my rail, and the owner IS the person who
            shared it — Share is an owner-only action (below). */}
        {shared && (
          <span className="chat-rail__item-by">
            Shared by <UserChip userId={item.owner} nameOnly />
          </span>
        )}
      </Link>
      {/* Rename / Share / Delete only for chats I own — the backend would 403
          those actions anyway; the "Shared by" line above carries the rest. */}
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
