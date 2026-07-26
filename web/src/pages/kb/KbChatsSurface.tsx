/**
 * The Chats surface (routes /kb/chats and /kb/chats/:chatId) — the left history
 * list plus a full-page conversation (KbChatView) for the selected/new thread.
 * Lifted out of the KB shell when it became a layout (#93). The open thread is
 * the URL: `:chatId === "new"` is the unsaved composer, a real id is an existing
 * thread, absent is "nothing selected". The doc viewer for followed citations
 * lives in the shell; we reach it through the Outlet context (useKbOutlet).
 */

import { useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { kbApi, type KbApi } from "../../api/kb";
import { DialogProvider } from "../../components/Dialog";
import { useT } from "../../lib/i18n";
import { KbChatsPage } from "./KbChatsPage";
import { KbChatView } from "./KbChatView";
import { useKbOutlet } from "./KbHome";

// URL sentinel for the unsaved new-chat composer (chat ids are never this).
const NEW_CHAT = "new";

export function KbChatsSurface({ client = kbApi }: { client?: KbApi }) {
  const { openCite } = useKbOutlet();
  const t = useT();
  const navigate = useNavigate();
  const { chatId: param } = useParams();
  // undefined = nothing selected; null = a new chat; string = an existing one.
  const chatId = param === NEW_CHAT ? null : (param ?? undefined);

  const [chatListVersion, setChatListVersion] = useState(0);

  // The mount key is DERIVED FROM THE URL, never bumped beside it. A counter
  // bumped in the click handler cannot work: `setState` is urgent while
  // react-router's `navigate` commits inside `startTransition`, so the new mount
  // lands one render BEFORE the new `:chatId` — freezing the thread we just left
  // into the fresh KbChatView (which snapshots `chatId` at mount). That is the
  // "click A, still see B, click again to see A" bug, and the same skew left
  // browser Back/Forward changing the URL without changing the thread.
  //
  // Two things the URL alone can't say, so they ride along as identity:
  //  - `newEpoch` distinguishes successive new chats (both are `/kb/chats/new`),
  //    so "New chat" from an unsaved composer still gives a clean one.
  //  - `bornId` is the id the CURRENT composer just minted (new → :realId). The
  //    URL changes but the thread does not, and remounting there would kill the
  //    stream writing it — so that id keeps the composer's mount identity.
  const [newEpoch, setNewEpoch] = useState(0);
  const bornId = useRef<string | null>(null);
  const mountKey =
    chatId === null || chatId === bornId.current ? `new:${newEpoch}` : `id:${chatId}`;

  // Explicitly open a thread (or a new one): the URL is the whole move.
  const openThread = (id: string | null) => {
    // Leaving the composer retires the id it minted; from here on that thread is
    // just another row (re-opening it must remount like any other).
    bornId.current = null;
    if (id === null) setNewEpoch((n) => n + 1);
    navigate(id === null ? "/kb/chats/new" : `/kb/chats/${encodeURIComponent(id)}`);
  };
  // A new thread (first message) should appear in the list right away and its
  // row should highlight — bump the list and swap the URL to the real id, while
  // `bornId` keeps the mount identity so the running stream survives.
  const onChatCreated = (id: string) => {
    bornId.current = id;
    setChatListVersion((v) => v + 1);
    navigate(`/kb/chats/${encodeURIComponent(id)}`, { replace: true });
  };

  return (
    <DialogProvider>
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
          <div className="kb-chats-split__empty">{t("kb.empty")}</div>
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
    </DialogProvider>
  );
}
