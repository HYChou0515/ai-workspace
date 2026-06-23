/**
 * The Chats surface (routes /kb/chats and /kb/chats/:chatId) — the left history
 * list plus a full-page conversation (KbChatView) for the selected/new thread.
 * Lifted out of the KB shell when it became a layout (#93). The open thread is
 * the URL: `:chatId === "new"` is the unsaved composer, a real id is an existing
 * thread, absent is "nothing selected". The doc viewer for followed citations
 * lives in the shell; we reach it through the Outlet context (useKbOutlet).
 */

import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { kbApi, type KbApi } from "../../api/kb";
import { KbChatsPage } from "./KbChatsPage";
import { KbChatView } from "./KbChatView";
import { useKbOutlet } from "./KbHome";

// URL sentinel for the unsaved new-chat composer (chat ids are never this).
const NEW_CHAT = "new";

export function KbChatsSurface({ client = kbApi }: { client?: KbApi }) {
  const { openCite } = useKbOutlet();
  const navigate = useNavigate();
  const { chatId: param } = useParams();
  // undefined = nothing selected; null = a new chat; string = an existing one.
  const chatId = param === NEW_CHAT ? null : (param ?? undefined);

  // KbChatView is keyed by `mountKey`, NOT by chatId, so a brand-new thread that
  // receives its real id mid-turn (new → :realId) doesn't remount and kill the
  // SSE stream. We bump it only on an EXPLICIT open (a row click / New chat),
  // never on the new→real-id transition.
  const [mountKey, setMountKey] = useState(0);
  const [chatListVersion, setChatListVersion] = useState(0);

  // Explicitly open a thread (or a new one): route there + force a fresh mount.
  const openThread = (id: string | null) => {
    setMountKey((k) => k + 1);
    navigate(id === null ? "/kb/chats/new" : `/kb/chats/${encodeURIComponent(id)}`);
  };
  // A new thread (first message) should appear in the list right away and its
  // row should highlight — bump the list, swap the URL to the real id, but DON'T
  // bump mountKey (no remount mid-stream).
  const onChatCreated = (id: string) => {
    setChatListVersion((v) => v + 1);
    navigate(`/kb/chats/${encodeURIComponent(id)}`, { replace: true });
  };

  return (
    <div className="kb-chats-split">
      <KbChatsPage
        client={client}
        selectedId={chatId ?? undefined}
        refreshSignal={chatListVersion}
        onOpenChat={(id) => openThread(id)}
        onNewChat={() => openThread(null)}
      />
      <div className="kb-chats-split__view">
        {chatId === undefined ? (
          <div className="kb-chats-split__empty">Select a conversation, or start a new one.</div>
        ) : (
          <KbChatView
            key={mountKey}
            chatId={chatId}
            onOpenCitation={openCite}
            onChatCreated={onChatCreated}
            client={client}
          />
        )}
      </div>
    </div>
  );
}
