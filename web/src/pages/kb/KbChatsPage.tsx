/**
 * KB chat history list — the threads you've had with the knowledge base.
 * Select one to open it (full-page KbChatView, not a drawer), start a new one,
 * pin, share, or delete. Used as the left pane of the Chats split in KbHome.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { kbApi, type KbApi, type KbChatSummary } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { Popover } from "../../components/Popover";
import { Skeleton } from "../../components/Skeleton";
import { UserChip } from "../../components/UserChip";
import { UserPicker } from "../../components/UserPicker";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { usePersistentSet } from "../../hooks/usePersistentSet";
import { kbChatLabel } from "./kbChatLabel";

type Tab = "all" | "pinned" | "shared";

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
  const {
    data: chats = [],
    refetch,
    isPending,
  } = useQuery({
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
  const pinned = usePersistentSet("kb:pinned-chats");
  const [tab, setTab] = useState<Tab>("all");

  const removeMut = useMutation({
    mutationFn: (chatId: string) => client.deleteChat(chatId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kb.chats }),
  });
  const shareMut = useMutation({
    mutationFn: (v: { chatId: string; userId: string; on: boolean }) =>
      v.on ? client.shareChat(v.chatId, [v.userId]) : client.unshareChat(v.chatId, v.userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kb.chats }),
  });

  const isMine = (c: KbChatSummary) => (c.owner ?? me) === me;
  const sharedCount = chats.filter((c) => !isMine(c)).length;
  const pinnedCount = chats.filter((c) => pinned.has(c.resource_id)).length;

  const shown = chats
    .filter((c) => {
      if (tab === "pinned") return pinned.has(c.resource_id);
      if (tab === "shared") return !isMine(c);
      return true;
    })
    .sort(
      (a, b) =>
        Number(pinned.has(b.resource_id)) - Number(pinned.has(a.resource_id)) ||
        // #357: recency — most recently updated first (matches the KbChat model's
        // ".info.updated_time → recency sort" intent), since titles are now mostly
        // blank (name_hint-labelled) and would all collate together.
        (b.updated_ms ?? 0) - (a.updated_ms ?? 0),
    );

  const tabs: [Tab, string, number][] = [
    ["all", "All", chats.length],
    ["pinned", "Pinned", pinnedCount],
    ["shared", "Shared with me", sharedCount],
  ];

  const row = (c: KbChatSummary) => {
    const owned = isMine(c);
    const isPinned = pinned.has(c.resource_id);
    const label = kbChatLabel(c); // #357: title → name_hint → timestamp
    return (
      <li key={c.resource_id} className="kb-chats__row">
        <button
          type="button"
          className={`kb-chats__open${c.resource_id === selectedId ? " is-active" : ""}`}
          onClick={() => onOpenChat?.(c.resource_id)}
        >
          <Icon name="chat" size={15} color="var(--text-paper-d)" />
          <span className="kb-chats__title">{label}</span>
          {owned ? (
            <span className="kb-chats__meta">{c.message_count} msgs</span>
          ) : (
            c.owner && <UserChip userId={c.owner} size={18} />
          )}
        </button>
        <button
          type="button"
          className={`kb-iconbtn${isPinned ? " is-pinned" : ""}`}
          aria-label={`${isPinned ? "Unpin" : "Pin"} ${label}`}
          aria-pressed={isPinned}
          onClick={() => pinned.toggle(c.resource_id)}
        >
          <Icon name="pin" size={14} />
        </button>
        {owned && (
          <>
            <Popover
              align="end"
              trigger={({ onClick }) => (
                <button type="button" className="kb-iconbtn" aria-label={`Share ${label}`} onClick={onClick}>
                  <Icon name="users" size={14} />
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
              aria-label={`Delete ${label}`}
              disabled={removeMut.isPending && removeMut.variables === c.resource_id}
              onClick={() => removeMut.mutate(c.resource_id)}
            >
              <Icon name="x" size={14} />
            </button>
          </>
        )}
      </li>
    );
  };

  return (
    <div className="kb-chats">
      <header className="kb-chats__head">
        <div>
          <h2 className="kb-chats__heading">Conversations</h2>
          {/* What Chats are + when to use (#162) — purpose, above the counts. */}
          <p className="kb-chats__lead">
            Ask questions across your collections. Every answer cites the documents it came from.
          </p>
          <p className="kb-chats__sub">
            {chats.length} {chats.length === 1 ? "chat" : "chats"}
            {sharedCount === 0 ? " · private to you" : ` · ${sharedCount} shared with you`}
          </p>
        </div>
        <button type="button" className="kb-btn kb-btn--primary" onClick={onNewChat}>
          <Icon name="sparkle" size={13} /> New chat
        </button>
      </header>

      <div className="kb-tabs kb-tabs--compact">
        {tabs.map(([id, label, count]) => (
          <button
            key={id}
            type="button"
            className={`kb-tab${tab === id ? " is-active" : ""}`}
            aria-pressed={tab === id}
            onClick={() => setTab(id)}
          >
            {label} <span className="kb-tab__count">{count}</span>
          </button>
        ))}
      </div>

      {isPending ? (
        <ul className="kb-chats__rows" aria-busy="true" data-testid="kb-chats-loading">
          {Array.from({ length: 5 }, (_, i) => (
            <li key={i} className="kb-chats__row kb-chats__row--skeleton">
              <Skeleton className="kb-skel--chat-row" />
            </li>
          ))}
        </ul>
      ) : chats.length === 0 ? (
        <p className="kb-cols__empty">No conversations yet — ask the agent something.</p>
      ) : shown.length === 0 ? (
        <p className="kb-cols__empty">No conversations in this view.</p>
      ) : (
        <ul className="kb-chats__rows">{shown.map(row)}</ul>
      )}
    </div>
  );
}
