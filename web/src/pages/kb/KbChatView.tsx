/**
 * KbChatView — a full-page KB conversation (NOT a drawer). This is what the
 * Chats page opens for both a new chat and an existing one; the drawer is only
 * for fast throwaway questions. Same chat core (KbChatPanel), page chrome.
 *
 * The header carries the thread's status (private / shared / pinned), a meta
 * line (messages · updated · started by), and actions: Share (read-only, via
 * the directory picker), Pin (local), and Export (download the thread as JSON).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { kbApi, type KbApi, type KbCitation } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { Popover } from "../../components/Popover";
import { UserChip } from "../../components/UserChip";
import { UserPicker } from "../../components/UserPicker";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { useIsSuperuser } from "../../hooks/useIsSuperuser";
import { usePersistentSet } from "../../hooks/usePersistentSet";
import { canManageAccess } from "../../lib/permission";
import { KbChatPanel } from "./KbChatPanel";

function timeAgo(ms: number): string {
  const s = Math.max(1, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

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
  const qc = useQueryClient();
  const me = useCurrentUser();
  const isSuperuser = useIsSuperuser();
  const pinned = usePersistentSet("kb:pinned-chats");
  // Freeze the thread id at mount: when a fresh thread gets its real id mid-turn
  // the parent updates the chatId prop, but we must NOT swap threads under the
  // running stream. The parent remounts (via key) for genuine thread switches.
  const [mountChatId] = useState(chatId);

  const { data: chat } = useQuery({
    queryKey: qk.kb.chat(mountChatId ?? "__new__"),
    queryFn: () => client.getChat(mountChatId as string),
    enabled: mountChatId != null,
  });
  // #357: label an unnamed thread by its first user message (name_hint) instead
  // of a generic "Chat", matching the list. A not-yet-created chat stays "New chat".
  const title = mountChatId == null ? "New chat" : chat?.title || chat?.name_hint || "Chat";

  const shareMut = useMutation({
    mutationFn: (v: { userId: string; on: boolean }) =>
      v.on
        ? client.shareChat(mountChatId as string, [v.userId])
        : client.unshareChat(mountChatId as string, v.userId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.kb.chat(mountChatId ?? "__new__") });
      void qc.invalidateQueries({ queryKey: qk.kb.chats });
    },
  });

  // Display fallback only — the "started by" chip. The MANAGE gate below never
  // reads it: `owner ?? me` treated an owner-less row as "mine".
  const owner = chat?.owner ?? me;
  // Share mirrors the backend change_permission gate: owner OR superuser.
  const canShare = chat != null && canManageAccess(chat.owner, me, isSuperuser);
  const sharedWith = chat?.shared_with ?? [];
  const isPinned = mountChatId != null && pinned.has(mountChatId);
  const msgs = chat?.messages.length ?? 0;
  const lastAt = chat?.messages.reduce((m, x) => Math.max(m, x.created_at ?? 0), 0) ?? 0;

  const exportJson = () => {
    if (!chat) return;
    // The `.chat.json` round-trip format (issue #39): re-uploadable to
    // any KB collection, where the BE runs the same insight extraction
    // the promote path does. Shape mirrors kb/chat_export.py — only
    // {title, messages:[{role, content, tool_name}]}, not the raw
    // KbChat dump (citations/metrics don't round-trip).
    // #357: fall back to the name_hint label so an unnamed thread exports with a
    // meaningful title / filename instead of a bare "chat".
    const name = chat.title || chat.name_hint || "chat";
    const payload = {
      title: name,
      messages: chat.messages.map((m) => ({
        role: m.role,
        content: m.content,
        tool_name: m.tool_name ?? "",
      })),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name.replace(/[^\w.-]+/g, "-")}.chat.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="kb-chatview">
      <header className="kb-chatview__head">
        <span className="kb-chatview__mark">
          <Icon name="sparkle" size={14} color="var(--accent)" />
        </span>
        <div className="kb-chatview__titles">
          {chat && (
            <div className="kb-chatview__chips">
              <span className="kb-chip">
                {sharedWith.length > 0 ? `shared · ${sharedWith.length}` : "private chat"}
              </span>
              {isPinned && (
                <span className="kb-chip kb-chip--accent">
                  <Icon name="pin" size={10} /> pinned
                </span>
              )}
            </div>
          )}
          <span className="kb-chatview__title">{title}</span>
          {chat && (
            <div className="kb-chatview__meta">
              <span>
                {msgs} {msgs === 1 ? "message" : "messages"}
              </span>
              {lastAt > 0 && (
                <>
                  <span aria-hidden>·</span>
                  <span>updated {timeAgo(lastAt)}</span>
                </>
              )}
              <span aria-hidden>·</span>
              <span className="kb-chatview__by">
                started by <UserChip userId={owner} size={16} />
              </span>
            </div>
          )}
        </div>

        {chat && (
          <div className="kb-chatview__actions">
            <button
              type="button"
              className={`kb-btn kb-btn--sm${isPinned ? " kb-btn--on" : ""}`}
              aria-pressed={isPinned}
              aria-label={isPinned ? "Unpin conversation" : "Pin conversation"}
              onClick={() => pinned.toggle(mountChatId as string)}
            >
              <Icon name="pin" size={13} /> {isPinned ? "Pinned" : "Pin"}
            </button>
            {canShare && (
              <Popover
                align="end"
                trigger={({ onClick, open }) => (
                  <button
                    type="button"
                    className="kb-btn kb-btn--sm"
                    aria-haspopup="menu"
                    aria-expanded={open}
                    onClick={onClick}
                  >
                    <Icon name="users" size={13} /> Share
                  </button>
                )}
              >
                {() => (
                  <div style={{ padding: 8 }}>
                    <div className="caps" style={{ padding: "0 4px 6px" }}>
                      Share read-only
                    </div>
                    <UserPicker
                      selected={sharedWith}
                      // The owner needs no share row, and neither does the person
                      // doing the sharing (a superuser managing someone else's chat).
                      exclude={[owner, me]}
                      onToggle={(userId) =>
                        shareMut.mutate({ userId, on: !sharedWith.includes(userId) })
                      }
                    />
                  </div>
                )}
              </Popover>
            )}
            <button type="button" className="kb-btn kb-btn--sm" onClick={exportJson}>
              <Icon name="download" size={13} /> Export
            </button>
          </div>
        )}
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
