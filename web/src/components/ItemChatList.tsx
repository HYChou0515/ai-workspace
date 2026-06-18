import type { ItemChatSummary } from "../api/itemChats";

/**
 * The item's chat list (topic-hub §3) — the multi-chat shell's tab rail. A chat's
 * label: its title if set, else "Chat" for the default / free chats and "Workflow"
 * for a run-driven chat (run_id set). Presentational — the parent owns selection.
 */
export function chatLabel(chat: ItemChatSummary): string {
  if (chat.title) return chat.title;
  if (chat.run_id) return "Workflow";
  return chat.is_default ? "Chat" : "Free chat";
}

export function ItemChatList({
  chats,
  activeChatId,
  onSelect,
}: {
  chats: ItemChatSummary[];
  activeChatId: string | null;
  onSelect: (chatId: string) => void;
}) {
  return (
    <ul className="item-chat-list" role="tablist" style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {chats.map((chat) => (
        <li key={chat.chat_id}>
          <button
            type="button"
            role="tab"
            className="item-chat-list__tab"
            aria-selected={chat.chat_id === activeChatId}
            data-testid={`chat-tab-${chat.chat_id}`}
            onClick={() => onSelect(chat.chat_id)}
          >
            {chatLabel(chat)}
            {chat.run_id ? <span aria-hidden> · run</span> : null}
          </button>
        </li>
      ))}
    </ul>
  );
}
