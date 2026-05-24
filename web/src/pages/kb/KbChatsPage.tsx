/**
 * KB chat history list — the threads you've had with the knowledge base.
 * Select one to open it (full-page KbChatView, not a drawer), start a new one,
 * or delete. Used as the left pane of the Chats split in KbHome.
 */

import { useCallback, useEffect, useState } from "react";

import { kbApi, type KbApi, type KbChatSummary } from "../../api/kb";
import { Icon } from "../../components/Icon";

export function KbChatsPage({
  client = kbApi,
  selectedId,
  refreshSignal,
  onOpenChat,
  onNewChat,
}: {
  client?: KbApi;
  selectedId?: string;
  /** Bump to force a re-fetch (e.g. a new chat was just started). */
  refreshSignal?: number;
  onOpenChat?: (chatId: string) => void;
  onNewChat?: () => void;
}) {
  const [chats, setChats] = useState<KbChatSummary[]>([]);

  const refresh = useCallback(async () => {
    setChats(await client.listChats());
  }, [client]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshSignal]);

  const remove = async (chatId: string) => {
    await client.deleteChat(chatId);
    await refresh();
  };

  return (
    <div className="kb-chats">
      <header className="kb-chats__head">
        <div>
          <h2 className="kb-docs__title">Conversations</h2>
          <p className="kb-docs__sub">
            {chats.length} {chats.length === 1 ? "chat" : "chats"}
          </p>
        </div>
        <button type="button" className="kb-btn kb-btn--primary" onClick={onNewChat}>
          <Icon name="sparkle" size={13} /> New chat
        </button>
      </header>

      {chats.length === 0 ? (
        <p className="kb-cols__empty">No conversations yet — ask the agent something.</p>
      ) : (
        <ul className="kb-chats__rows">
          {chats.map((c) => (
            <li key={c.resource_id} className="kb-chats__row">
              <button
                type="button"
                className={`kb-chats__open${c.resource_id === selectedId ? " is-active" : ""}`}
                onClick={() => onOpenChat?.(c.resource_id)}
              >
                <Icon name="chat" size={15} color="var(--text-paper-d)" />
                <span className="kb-chats__title">{c.title}</span>
                <span className="kb-chats__meta">{c.message_count} msgs</span>
              </button>
              <button
                type="button"
                className="kb-iconbtn"
                aria-label={`Delete ${c.title}`}
                onClick={() => remove(c.resource_id)}
              >
                <Icon name="x" size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
