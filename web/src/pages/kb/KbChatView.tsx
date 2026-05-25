/**
 * KbChatView — a full-page KB conversation (NOT a drawer). This is what the
 * Chats page opens for both a new chat and an existing one; the drawer is only
 * for fast throwaway questions. Same chat core (KbChatPanel), page chrome.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { kbApi, type KbApi, type KbCitation } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { KbChatPanel } from "./KbChatPanel";

export function KbChatView({
  chatId,
  onOpenCitation,
  onChatCreated,
  client = kbApi,
}: {
  /** null = a new conversation; otherwise continue this thread. */
  chatId: string | null;
  onOpenCitation?: (c: KbCitation) => void;
  onChatCreated?: (chatId: string) => void;
  client?: KbApi;
}) {
  // Freeze the thread id at mount: when a fresh thread gets its real id mid-turn
  // the parent updates the chatId prop, but we must NOT swap threads under the
  // running stream. The parent remounts (via key) for genuine thread switches.
  const [mountChatId] = useState(chatId);

  const { data: chat } = useQuery({
    queryKey: qk.kb.chat(mountChatId ?? "__new__"),
    queryFn: () => client.getChat(mountChatId as string),
    enabled: mountChatId != null,
  });
  const title = mountChatId == null ? "New chat" : chat?.title || "Chat";

  return (
    <div className="kb-chatview">
      <header className="kb-chatview__head">
        <span className="kb-chatview__mark">
          <Icon name="sparkle" size={14} color="var(--accent)" />
        </span>
        <span className="kb-chatview__title">{title}</span>
      </header>
      <KbChatPanel
        chatId={mountChatId}
        onOpenCitation={onOpenCitation}
        onChatCreated={onChatCreated}
        client={client}
      />
    </div>
  );
}
