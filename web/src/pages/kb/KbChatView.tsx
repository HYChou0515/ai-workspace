/**
 * KbChatView — a full-page KB conversation (NOT a drawer). This is what the
 * Chats page opens for both a new chat and an existing one; the drawer is only
 * for fast throwaway questions. Same chat core (KbChatPanel), page chrome.
 */

import { useEffect, useState } from "react";

import { kbApi, type KbApi, type KbCitation } from "../../api/kb";
import { Icon } from "../../components/Icon";
import { KbChatPanel } from "./KbChatPanel";

export function KbChatView({
  chatId,
  onOpenCitation,
  client = kbApi,
}: {
  /** null = a new conversation; otherwise continue this thread. */
  chatId: string | null;
  onOpenCitation?: (c: KbCitation) => void;
  client?: KbApi;
}) {
  const [title, setTitle] = useState<string>("New chat");

  useEffect(() => {
    let on = true;
    if (chatId == null) {
      setTitle("New chat");
      return;
    }
    client.getChat(chatId).then((c) => on && setTitle(c.title || "Chat"));
    return () => {
      on = false;
    };
  }, [chatId, client]);

  return (
    <div className="kb-chatview">
      <header className="kb-chatview__head">
        <span className="kb-chatview__mark">
          <Icon name="sparkle" size={14} color="var(--accent)" />
        </span>
        <span className="kb-chatview__title">{title}</span>
      </header>
      {/* key remounts the chat core when switching threads (or new) */}
      <KbChatPanel
        key={chatId ?? "new"}
        chatId={chatId}
        onOpenCitation={onOpenCitation}
        client={client}
      />
    </div>
  );
}
