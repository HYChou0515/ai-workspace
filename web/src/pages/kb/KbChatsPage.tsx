/**
 * KB chat history list — the threads you've had with the knowledge base.
 * Select one to open it (full-page KbChatView, not a drawer), start a new one,
 * or delete. Used as the left pane of the Chats split in KbHome.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { kbApi, type KbApi, type KbChatSummary } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { Popover } from "../../components/Popover";
import { UserChip } from "../../components/UserChip";
import { UserPicker } from "../../components/UserPicker";
import { useCurrentUser } from "../../hooks/useCurrentUser";

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
  const qc = useQueryClient();
  const { data: chats = [], refetch } = useQuery({
    queryKey: qk.kb.chats,
    queryFn: () => client.listChats(),
  });

  // Parent bumps refreshSignal when a new chat is started → force a refetch
  // (skip the first run; the query already fetches on mount).
  const firstRun = useRef(true);
  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    void refetch();
  }, [refreshSignal, refetch]);

  const me = useCurrentUser();
  const removeMut = useMutation({
    mutationFn: (chatId: string) => client.deleteChat(chatId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kb.chats }),
  });
  const shareMut = useMutation({
    mutationFn: (v: { chatId: string; userId: string; on: boolean }) =>
      v.on ? client.shareChat(v.chatId, [v.userId]) : client.unshareChat(v.chatId, v.userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kb.chats }),
  });

  const mine = chats.filter((c) => (c.owner ?? me) === me);
  const shared = chats.filter((c) => c.owner && c.owner !== me);

  const row = (c: KbChatSummary, owned: boolean) => (
    <li key={c.resource_id} className="kb-chats__row">
      <button
        type="button"
        className={`kb-chats__open${c.resource_id === selectedId ? " is-active" : ""}`}
        onClick={() => onOpenChat?.(c.resource_id)}
      >
        <Icon name="chat" size={15} color="var(--text-paper-d)" />
        <span className="kb-chats__title">{c.title}</span>
        {owned ? (
          <span className="kb-chats__meta">{c.message_count} msgs</span>
        ) : (
          c.owner && <UserChip userId={c.owner} size={18} />
        )}
      </button>
      {owned && (
        <>
          <Popover
            align="end"
            trigger={({ onClick }) => (
              <button type="button" className="kb-iconbtn" aria-label={`Share ${c.title}`} onClick={onClick}>
                <Icon name="user" size={14} />
              </button>
            )}
          >
            {() => (
              <div style={{ padding: 8 }}>
                <div className="caps" style={{ padding: "0 4px 6px" }}>
                  Share read-only
                </div>
                <UserPicker
                  selected={c.shared_with ?? []}
                  exclude={[me]}
                  onToggle={(userId) =>
                    shareMut.mutate({
                      chatId: c.resource_id,
                      userId,
                      on: !(c.shared_with ?? []).includes(userId),
                    })
                  }
                />
              </div>
            )}
          </Popover>
          <button
            type="button"
            className="kb-iconbtn"
            aria-label={`Delete ${c.title}`}
            onClick={() => removeMut.mutate(c.resource_id)}
          >
            <Icon name="x" size={14} />
          </button>
        </>
      )}
    </li>
  );

  return (
    <div className="kb-chats">
      <header className="kb-chats__head">
        <div>
          <h2 className="kb-docs__title">Conversations</h2>
          <p className="kb-docs__sub">
            {mine.length} {mine.length === 1 ? "chat" : "chats"}
          </p>
        </div>
        <button type="button" className="kb-btn kb-btn--primary" onClick={onNewChat}>
          <Icon name="sparkle" size={13} /> New chat
        </button>
      </header>

      {chats.length === 0 ? (
        <p className="kb-cols__empty">No conversations yet — ask the agent something.</p>
      ) : (
        <>
          <ul className="kb-chats__rows">{mine.map((c) => row(c, true))}</ul>
          {shared.length > 0 && (
            <>
              <div className="caps" style={{ padding: "12px 14px 4px" }}>
                Shared with me
              </div>
              <ul className="kb-chats__rows">{shared.map((c) => row(c, false))}</ul>
            </>
          )}
        </>
      )}
    </div>
  );
}
