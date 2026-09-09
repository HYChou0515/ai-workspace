import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { kbApi, type KbApi, type KbImageInput } from "../api/kb";
import { qk } from "../api/queryKeys";
import { isTerminal } from "../events";
import { getKbAgentName } from "../lib/kbAgent";
import {
  getStored as getKbEnhancementSelection,
  toBodyEnhancements,
} from "../lib/kbEnhancementMode";
import { getReasoningEffort } from "../lib/reasoningEffort";
import { getKbDisclosure } from "../lib/kbDisclosure";
import { getKbSearchMax } from "../lib/kbSearchMax";
import { getKbWikiMax } from "../lib/kbWikiMax";
import {
  EMPTY_LOG,
  drawOwnAsk,
  retractOwnAsk,
  type AgentLog,
  reduceAgent,
} from "../pages/investigation/agentLog";
import { useChatLog } from "./useChatLog";
import { useCurrentUser } from "./useCurrentUser";

/**
 * Drives one KB chat thread, reusing the RCA agent-log machinery so the KB chat
 * renders identically (foldable reasoning, tool-call cards, live token metrics).
 *
 * The SSE stream is folded through `reduceAgent` for live progress; on a
 * terminal event we refetch the thread and reconcile it — that persisted view
 * carries the resolved `[n]` citations (the stream doesn't).
 *
 * The POST used to BE the stream, which is what made a follow-up question
 * impossible: a body held open until the answer finished could not also let the
 * next message queue behind it, so the backend cancelled instead, and the
 * composer covered that by refusing to send at all. Now the send is a 202 and
 * the events arrive on a subscription that outlives any one turn.
 *
 * `client` is injectable so the hook is unit-testable against the mock.
 */
export type UseKbChat = {
  chatId: string | null;
  log: AgentLog;
  send: (content: string, image?: KbImageInput) => Promise<void>;
  cancel: () => void;
  reset: () => void;
};

export function useKbChat({
  collectionIds,
  excludedCollectionIds = [],
  chatId: initialChatId = null,
  client = kbApi,
  onChatCreated,
}: {
  /** The explicitly-specified (non-global) collections for this thread. */
  collectionIds: string[];
  /** Global collections the user un-checked — excluded from this thread's scope. */
  excludedCollectionIds?: string[];
  chatId?: string | null;
  client?: KbApi;
  /** Fired when the first message creates the thread (so the list can refresh). */
  onChatCreated?: (chatId: string) => void;
}): UseKbChat {
  const qc = useQueryClient();
  const [chatId, setChatId] = useState<string | null>(initialChatId);
  const currentUser = useCurrentUser();

  const { log, setLog, reconcile } = useChatLog({
    threadKey: initialChatId,
    // Shares the cache with KbChatView's title query.
    queryKey: qk.kb.chat(initialChatId ?? "__new__"),
    getThread: () => client.getChat(initialChatId as string),
  });

  // Whether this hook is still mounted. The LOG's writes are guarded by
  // `useChatLog`, where that state is made; this ref covers the one piece of
  // state this hook owns itself — `chatId`, written after an await, when a
  // thread finishes being created for a view that has already gone.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    setChatId(initialChatId);
  }, [initialChatId]);

  // ---------------------------------------------------------------- subscribe
  //
  // One subscription per chat, living across turns — that is the whole point.
  // `subRef` holds the chat it is attached to so `send` can start one for a
  // thread it has only just created, without waiting a render for this effect.
  const subRef = useRef<{ id: string; controller: AbortController } | null>(null);

  const attach = useCallback(
    (id: string) => {
      if (subRef.current?.id === id) return;
      subRef.current?.controller.abort();
      const controller = new AbortController();
      subRef.current = { id, controller };
      void (async () => {
        try {
          for await (const ev of client.subscribeChat(id, controller.signal)) {
            setLog((prev) => reduceAgent(prev, ev));
            if (isTerminal(ev)) {
              // The persisted thread is what carries the resolved [n]
              // citations; the stream never has them. Reconcile, never
              // replace — it must not cost the user the answer they just
              // watched arrive.
              const fresh = await client.getChat(id);
              qc.setQueryData(qk.kb.chat(id), fresh);
              reconcile(fresh);
            }
          }
        } catch (err: unknown) {
          if (controller.signal.aborted) return;
          const msg = err instanceof Error ? err.message : String(err);
          // Say so rather than going quiet: a stream that dropped looks exactly
          // like a chat where nothing is happening, and the answer simply stops
          // growing.
          setLog((prev) => ({ ...prev, error: msg }));
        }
      })();
    },
    [client, qc, reconcile, setLog],
  );

  useEffect(() => {
    if (chatId == null) return;
    attach(chatId);
    return () => {
      subRef.current?.controller.abort();
      subRef.current = null;
    };
  }, [chatId, attach]);

  // --------------------------------------------------------------------- send
  const send = useCallback(
    async (content: string, image?: KbImageInput) => {
      const trimmed = content.trim();
      // #513 P10: an image-only message (no text) is a valid turn — the VLM
      // description carries the query — so gate on text OR image.
      //
      // NOT on `log.streaming`. The backend serializes KB turns now, so a
      // question asked during an answer simply queues; refusing it here is what
      // made the gesture produce no reaction at all — no bubble, no cleared box,
      // no reason given, which reads as the app being dead.
      if (!trimmed && !image) return;

      let id = chatId;
      if (id == null) {
        id = (await client.createChat("", collectionIds, excludedCollectionIds)).resource_id;
        // The view may have left while the thread was being created — the
        // drawer closed, another thread clicked. Skip only what needs a live
        // view: the state write and the callback that NAVIGATES, which would
        // yank whoever is reading back to a thread they just left.
        //
        // The question itself still goes. The thread exists on the server by
        // now, so dropping it leaves an empty chat in the list and the thing
        // the person typed nowhere at all.
        if (mounted.current) {
          setChatId(id);
          onChatCreated?.(id);
        }
        void qc.invalidateQueries({ queryKey: qk.kb.chats });
      }
      // Attach BEFORE sending, and directly rather than via the effect above:
      // the effect runs a render later, and the events this send is about to
      // provoke do not wait for React.
      attach(id);

      // Draw the question and lock in "a turn is in flight" at once. The
      // `user_message` broadcast adopts this entry when it lands, so it stays
      // one bubble — keyed on author, which is why this must be the id the
      // backend will stamp and not a display name like "You".
      setLog((prev) => drawOwnAsk(prev, { author: currentUser, content: trimmed }));
      try {
        await client.sendMessage({
          chatId: id,
          content: trimmed,
          // #513 P10: the server VLM-describes this transient image into the query.
          image,
          reasoningEffort: getReasoningEffort() ?? undefined,
          enhancements: toBodyEnhancements(getKbEnhancementSelection()),
          agentName: getKbAgentName() ?? undefined,
          // #334: per-message cap on this reply's kb_search calls (0 = no search).
          maxKbSearches: getKbSearchMax(),
          disclosure: getKbDisclosure(),
          // #506: per-message cap on this reply's wiki greps (replaces the toggle).
          maxWikiSearches: getKbWikiMax(),
        });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        // Take the drawn question back: the send was refused, so no broadcast
        // will ever adopt that entry and no turn is coming for it. Left drawn it
        // would sit there with the error beside it saying it was never sent.
        setLog((prev) => ({
          ...prev,
          streaming: false,
          error: msg,
          entries: retractOwnAsk(prev, { author: currentUser, content: trimmed }).entries,
        }));
      }
    },
    [
      chatId,
      collectionIds,
      excludedCollectionIds,
      client,
      currentUser,
      onChatCreated,
      qc,
      setLog,
      attach,
    ],
  );

  const cancel = useCallback(() => {
    // Only an already-created thread has a server turn to cancel. The
    // subscription STAYS: Stop ends a turn, not the conversation, and the
    // cancellation itself arrives on it.
    if (chatId) void client.cancelMessage(chatId);
    // #49: flip out of "streaming" immediately so Stop unblocks the composer
    // even if the teardown is slow.
    setLog((prev) => ({ ...prev, streaming: false }));
  }, [chatId, client, setLog]);

  const reset = useCallback(() => {
    subRef.current?.controller.abort();
    subRef.current = null;
    setChatId(null);
    setLog(EMPTY_LOG);
  }, [setLog]);

  return { chatId, log, send, cancel, reset };
}
