import { useEffect, useRef, useState } from "react";

import type { ItemChatSummary } from "../api/itemChats";
import { relativeTime } from "../api/types";
import { chatLabel } from "./chatLabel";
import { chatStatusBadge } from "./chatStatusBadge";
import { Icon } from "./Icon";

/**
 * The item's chat switcher (#132) — a compact dropdown that replaces the wrapping
 * tab rail. The trigger shows the active chat's name; the menu lists every chat
 * (most-recent first, already ordered by the API) with a `⚙` + status badge on
 * workflow chats and a relative-activity time, plus a footer that opens the manage
 * modal. Presentational — the parent owns selection + the modal.
 */
function activityLabel(ms: number | null): string {
  return ms == null ? "" : relativeTime(new Date(ms).toISOString());
}

function ChatRow({ chat }: { chat: ItemChatSummary }) {
  const badge = chatStatusBadge(chat.status);
  return (
    <>
      {chat.run_id && (
        <Icon name="settings" size={12} color="var(--text-paper-d)" />
      )}
      <span className="chat-switcher__label">{chatLabel(chat)}</span>
      {badge && (
        <span className={`chat-switcher__badge chat-switcher__badge--${badge.tone}`}>
          {badge.symbol} {badge.label}
        </span>
      )}
      <span className="chat-switcher__time">{activityLabel(chat.last_activity_ms)}</span>
    </>
  );
}

export function ChatSwitcher({
  chats,
  activeChatId,
  onSelect,
  onManage,
}: {
  chats: ItemChatSummary[];
  activeChatId: string | null;
  onSelect: (chatId: string) => void;
  onManage: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Close on outside click / Escape (a menu, not a modal).
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const active = chats.find((c) => c.chat_id === activeChatId) ?? null;

  const select = (chatId: string) => {
    setOpen(false);
    onSelect(chatId);
  };

  return (
    <div className="chat-switcher" ref={rootRef}>
      <button
        type="button"
        className="chat-switcher__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        data-testid="chat-switcher-trigger"
      >
        <span className="chat-switcher__current">{active ? chatLabel(active) : "No chat"}</span>
        <Icon name="chev_d" size={12} color="var(--text-paper-d)" />
      </button>
      {open && (
        <div role="menu" className="chat-switcher__menu" data-testid="chat-switcher-menu">
          <ul className="chat-switcher__list" role="presentation">
            {chats.map((chat) => (
              <li key={chat.chat_id}>
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={chat.chat_id === activeChatId}
                  className="chat-switcher__item"
                  data-testid={`chat-switcher-item-${chat.chat_id}`}
                  onClick={() => select(chat.chat_id)}
                >
                  <ChatRow chat={chat} />
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            role="menuitem"
            className="chat-switcher__manage"
            data-testid="chat-switcher-manage"
            onClick={() => {
              setOpen(false);
              onManage();
            }}
          >
            Manage all chats…
          </button>
        </div>
      )}
    </div>
  );
}
