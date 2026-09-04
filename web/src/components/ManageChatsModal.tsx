import { useEffect, useRef, useState } from "react";

import type { ItemChatSummary } from "../api/itemChats";
import { relativeTime } from "../api/types";
import { chatLabel } from "./chatLabel";
import { chatStatusBadge } from "./chatStatusBadge";
import { Icon } from "./Icon";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { ModalShell } from "./ModalShell";

/**
 * The manage-all-chats modal (#132) — the large surface for the "many chats" case.
 * Lists every chat with a search filter and, per row, explicit Switch / Rename
 * (inline) / Delete (two-step confirm) buttons. Deleting a workflow chat also stops
 * its run (the confirm copy says so); the backend does the cancel. Presentational —
 * the parent wires select / rename / delete.
 */
function activity(ms: number | null): string {
  return ms == null ? "—" : relativeTime(new Date(ms).toISOString());
}

function ChatTypeCell({ chat }: { chat: ItemChatSummary }) {
  const badge = chatStatusBadge(chat.status);
  if (!chat.run_id) return <span className="manage-chats__type">Chat</span>;
  return (
    <span className="manage-chats__type manage-chats__type--workflow">
      <Icon name="settings" size={12} color="var(--text-paper-d)" />
      Workflow
      {badge && (
        <span className={`manage-chats__badge manage-chats__badge--${badge.tone}`}>
          {badge.symbol} {badge.label}
        </span>
      )}
    </span>
  );
}

function ChatRow({
  chat,
  active,
  onSelect,
  onClose,
  onRename,
  onDelete,
  onEditingChange,
}: {
  chat: ItemChatSummary;
  active: boolean;
  onSelect: (id: string) => void;
  onClose: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  /** #779: tell the modal a row is mid-rename, so leaving asks first. */
  onEditingChange?: (editing: boolean) => void;
}) {
  const [editing, setEditingState] = useState(false);
  const setEditing = (v: boolean) => {
    setEditingState(v);
    onEditingChange?.(v);
  };
  // Report "no longer editing" on unmount too. The row can vanish without ever
  // calling setEditing(false) — start a rename, then type in the search box and
  // this row falls out of the filtered list. Without this the modal believed a
  // rename was still open forever after, and every exit raised a prompt about
  // work that no longer existed. A guard that fires over nothing is one people
  // learn to click through.
  //
  // Through a ref because the caller passes a fresh closure each render; as an
  // effect dependency that would re-run the cleanup on every render and clear
  // the flag while the rename is still open.
  const notify = useRef(onEditingChange);
  notify.current = onEditingChange;
  useEffect(() => () => notify.current?.(false), []);
  const [draft, setDraft] = useState(chat.title);
  const [confirming, setConfirming] = useState(false);

  const id = chat.chat_id;
  return (
    <tr data-testid={`manage-chat-row-${id}`} className={active ? "manage-chats__row--active" : ""}>
      <td className="manage-chats__name">
        {editing ? (
          <input
            className="manage-chats__rename"
            data-testid={`manage-rename-input-${id}`}
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                onRename(id, draft);
                setEditing(false);
              }
              if (e.key === "Escape") setEditing(false);
            }}
          />
        ) : (
          chatLabel(chat)
        )}
      </td>
      <td>
        <ChatTypeCell chat={chat} />
      </td>
      <td className="manage-chats__activity">{activity(chat.last_activity_ms)}</td>
      <td className="manage-chats__count">{chat.message_count}</td>
      <td className="manage-chats__actions">
        {editing ? (
          <button
            type="button"
            data-testid={`manage-rename-save-${id}`}
            onClick={() => {
              onRename(id, draft);
              setEditing(false);
            }}
          >
            Save
          </button>
        ) : confirming ? (
          <>
            <span className="manage-chats__confirm-note">
              {chat.run_id ? "Delete? Its workflow will be stopped." : "Delete this chat?"}
            </span>
            <button
              type="button"
              className="manage-chats__danger"
              data-testid={`manage-delete-confirm-${id}`}
              onClick={() => onDelete(id)}
            >
              Confirm
            </button>
            <button type="button" onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              data-testid={`manage-switch-${id}`}
              onClick={() => {
                onSelect(id);
                onClose();
              }}
            >
              Switch
            </button>
            <button
              type="button"
              data-testid={`manage-edit-${id}`}
              onClick={() => {
                setDraft(chat.title);
                setEditing(true);
              }}
            >
              Rename
            </button>
            <button
              type="button"
              className="manage-chats__danger"
              data-testid={`manage-delete-${id}`}
              onClick={() => setConfirming(true)}
            >
              Delete
            </button>
          </>
        )}
      </td>
    </tr>
  );
}

export function ManageChatsModal({
  chats,
  activeChatId,
  onClose,
  onSelect,
  onRename,
  onDelete,
}: {
  chats: ItemChatSummary[];
  activeChatId: string | null;
  onClose: () => void;
  onSelect: (chatId: string) => void;
  onRename: (chatId: string, title: string) => void;
  onDelete: (chatId: string) => void;
}) {
  const [query, setQuery] = useState("");
  // #779: which row is mid-rename (null = none). One id, not a set: only one
  // row can hold the inline editor at a time.
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const attemptClose = useDirtyClose(renamingId !== null, onClose);
  const q = query.trim().toLowerCase();
  const shown = q ? chats.filter((c) => chatLabel(c).toLowerCase().includes(q)) : chats;

  return (
    <ModalShell
      onClose={attemptClose}
      ariaLabel="Manage chats"
      data-testid="manage-chats-modal"
      width={720}
      maxWidth="100%"
      panelClassName="manage-chats__dialog"
    >
      <header className="manage-chats__header">
        <h2 className="manage-chats__title">Manage chats</h2>
        <button
          type="button"
          className="manage-chats__close"
          aria-label="Close"
          data-testid="manage-chats-close"
          onClick={attemptClose}
        >
          <Icon name="x" size={14} color="var(--text-paper-d)" />
        </button>
      </header>
      <input
        className="manage-chats__search"
        type="search"
        placeholder="Search chats"
        aria-label="Search chats"
        data-testid="manage-chats-search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="manage-chats__tableWrap">
        <table className="manage-chats__table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>Activity</th>
              <th>Messages</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {shown.map((chat) => (
              <ChatRow
                key={chat.chat_id}
                chat={chat}
                active={chat.chat_id === activeChatId}
                onSelect={onSelect}
                onClose={onClose}
                onRename={onRename}
                onDelete={onDelete}
                onEditingChange={(on) =>
                  // Only this row may clear its own flag — an unmounting row
                  // must not cancel a rename that another row now owns.
                  setRenamingId((cur) =>
                    on ? chat.chat_id : cur === chat.chat_id ? null : cur,
                  )
                }
              />
            ))}
          </tbody>
        </table>
        {shown.length === 0 && <p className="manage-chats__empty">No chats match.</p>}
      </div>
    </ModalShell>
  );
}
