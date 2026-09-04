/**
 * Simplified share for a chat (#chat-private #5): pick people and/or groups —
 * they get to read + take part in the chat, and it shows up in THEIR chat list
 * (their "shared with me"). Deliberately NOT the full manage-access editor: no
 * visibility radios, no per-verb role ladder. It reuses the same backend
 * permission endpoint, granting the read/converse verbs to the chosen subjects.
 */

import { useRef, useState } from "react";

import type { AppItem } from "../api/types";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { usePickableGroups } from "../hooks/usePickableGroups";
import { useSetItemPermission } from "../hooks/useResources";
import { type ItemPermission, parseItemPermission } from "../lib/itemPermission";
import { ModalShell } from "./ModalShell";
import { UserPicker } from "./UserPicker";

function subjectsFrom(perm: ItemPermission | undefined, prefix: "user:" | "group:"): string[] {
  return (perm?.read_meta ?? [])
    .filter((s) => s.startsWith(prefix))
    .map((s) => s.slice(prefix.length));
}

export function ShareChatDialog({
  slug,
  item,
  onClose,
}: {
  slug: string;
  item: AppItem;
  onClose: () => void;
}) {
  const current = parseItemPermission((item as Record<string, unknown>).permission);
  const groups = usePickableGroups();
  const { setPermissionAsync, isPending, error } = useSetItemPermission(slug, item.resource_id);
  const [users, setUsers] = useState<string[]>(() => subjectsFrom(current, "user:"));
  const [groupIds, setGroupIds] = useState<string[]>(() => subjectsFrom(current, "group:"));

  const toggle = (set: (fn: (s: string[]) => string[]) => void, id: string) =>
    set((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  // #779: the picks are not sent until Share, so closing means the people you
  // chose were simply never granted anything — and nothing says so.
  const initialRef = useRef(
    JSON.stringify({ users: subjectsFrom(current, "user:"), groupIds: subjectsFrom(current, "group:") }),
  );
  const attemptClose = useDirtyClose(
    JSON.stringify({ users, groupIds }) !== initialRef.current,
    onClose,
  );

  const save = async () => {
    const subjects = [...users.map((u) => `user:${u}`), ...groupIds.map((g) => `group:${g}`)];
    // Grant read + converse to the chosen subjects; back to private if none.
    // Other verbs / change_permission are preserved verbatim from the current
    // permission so a share never loosens anything the owner set elsewhere.
    const perm: ItemPermission = {
      ...(current ?? { visibility: "private" }),
      visibility: subjects.length ? "restricted" : "private",
      read_meta: subjects,
      read_chat: subjects,
      read_content: subjects,
      converse: subjects,
    };
    await setPermissionAsync(perm);
    onClose();
  };

  return (
    <ModalShell onClose={attemptClose} ariaLabel="Share chat">
      <div className="chat-share">
        <div className="chat-share__title">Share this chat</div>
        <div className="chat-share__hint">
          Pick people or groups — they’ll see it in their chats and can read + reply.
        </div>

        <div className="chat-share__section">People</div>
        <UserPicker
          selected={users}
          onToggle={(id) => toggle(setUsers, id)}
          exclude={[item.owner]}
          placeholder="Add a person…"
        />

        <div className="chat-share__section">Groups</div>
        <div className="chat-share__groups">
          {groups.length === 0 ? (
            <div className="chat-share__none">No groups available</div>
          ) : (
            groups.map((g) => (
              <label key={g.resource_id} className="chat-share__group">
                <input
                  type="checkbox"
                  checked={groupIds.includes(g.resource_id)}
                  onChange={() => toggle(setGroupIds, g.resource_id)}
                />
                {g.name}
              </label>
            ))
          )}
        </div>

        {error && <div className="chat-share__error">{error}</div>}
        <div className="chat-share__actions">
          <button type="button" className="btn" data-size="sm" onClick={attemptClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn"
            data-variant="primary"
            data-size="sm"
            onClick={save}
            disabled={isPending}
          >
            Share
          </button>
        </div>
      </div>
    </ModalShell>
  );
}
