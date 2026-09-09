import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { kbApi, type KbApi, type KbImageInput } from "../api/kb";
import { qk } from "../api/queryKeys";
import { eventId, eventSeq, isTerminal } from "../events";
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

/** How many delivered event ids a viewer remembers, for the replay de-dupe —
 * matched to the server's replay ring, since what this guards against is a
 * re-delivery of something the server still holds. */
const SEEN_IDS_MAX = 2000;

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
      // Abort here too, not only in the `[chatId, attach]` effect. `send`
      // attaches a subscription for a chat it has just CREATED, before that
      // effect has committed for the new id — the committed one ran with
      // `chatId == null` and returned without registering a cleanup. Unmounting
      // in that window used to leak a dead generator; it would now leak an
      // endless reconnect loop hitting the stream every 15s for the life of the
      // page.
      subRef.current?.controller.abort();
      subRef.current = null;
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
        let backoff = 1000;
        // A subscription that ENDS is not a subscription that is finished. A
        // clean EOF throws nothing — an idle proxy cutting the connection, a pod
        // rollover, `close_streams` — so a loop that only caught errors left the
        // chat permanently deaf: the next send returned 202, `streaming` stayed
        // true, and no answer ever rendered again until the component remounted.
        // Only an abort (unmount / thread switch) stops this.
        let firstConnect = true;
        let maxSeq: number | undefined;
        const seen = new Set<string>();
        while (!controller.signal.aborted) {
          // The first connect of this subscription replays nothing; every later
          // one RESUMES from the last seq seen, so the events emitted during the
          // gap come back rather than being lost.
          const since = firstConnect ? undefined : maxSeq;
          firstConnect = false;
          try {
            for await (const ev of client.subscribeChat(id, controller.signal, since)) {
              const seq = eventSeq(ev);
              if (seq !== undefined && (maxSeq === undefined || seq > maxSeq)) maxSeq = seq;
              // A replay re-delivers what this viewer already folded. An event
              // with no id is always folded — dropping those would blank the
              // stream against a backend that predates them.
              const evId = eventId(ev);
              if (evId !== undefined) {
                if (seen.has(evId)) continue;
                seen.add(evId);
                if (seen.size > SEEN_IDS_MAX) {
                  let drop = seen.size - SEEN_IDS_MAX;
                  for (const old of seen) {
                    if (drop-- <= 0) break;
                    seen.delete(old); // a Set iterates oldest-first
                  }
                }
              }
              // Only a NEW event counts as health. Resetting on a re-delivered
              // replay lets a pod that dies right after serving one reconnect
              // every second forever — the backoff never engages in the single
              // scenario it exists for.
              backoff = 1000;
              // …and this stream is working, so a message saying it dropped is
              // no longer true. `message_delta` only clears an error it raised
              // itself (`errorFromTurn`), and `reconcileSnapshot` carries one
              // across, so that notice would otherwise stay pinned above the
              // whole answer streaming in below it.
              setLog((prev) => (prev.error === null ? prev : { ...prev, error: null }));
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
            // Say so rather than going quiet: a stream that dropped looks
            // exactly like a chat where nothing is happening, and the answer
            // simply stops growing.
            setLog((prev) => ({ ...prev, error: msg }));
          }
          if (controller.signal.aborted) return;
          await new Promise<void>((resolve) => setTimeout(resolve, backoff));
          if (controller.signal.aborted) return;
          // RE-HYDRATE before resuming, exactly as `useChatSession` does after
          // its own backoff — and for the reason its comment gives: the replay
          // ring lives on the pod's session, so a reconnect that lands on a
          // DIFFERENT pod (the rollover this loop exists for) resumes into an
          // empty ring. The terminal event is then gone for good, and without
          // this read `streaming` would stay true forever with the finished
          // answer sitting unread in the store — the same symptom as never
          // reconnecting at all, moved one step later.
          const fresh = await client.getChat(id).catch(() => null);
          if (fresh && !controller.signal.aborted) {
            qc.setQueryData(qk.kb.chat(id), fresh);
            // Reconcile, not replace: a drop MID-turn re-hydrates a thread that
            // does not yet contain what is on screen.
            reconcile(fresh);
            // The evidence that the turn ended has to be an ANSWER THAT
            // ARRIVED. "Not a user message" is not the same thing — a `notice`
            // (#624) is persisted before the model is even called and stands
            // through the whole time-to-first-token window.
            const last = fresh.messages[fresh.messages.length - 1];
            if (last !== undefined && last.role === "assistant") {
              setLog((prev) => ({ ...prev, streaming: false }));
            }
          }
          backoff = Math.min(backoff * 2, 15000);
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
      // provoke do not wait for React. Skipped when the view has already gone —
      // nothing would abort that subscription, since the effect never registered
      // a cleanup for a controller it did not create.
      if (mounted.current) attach(id);

      // Draw the question and lock in "a turn is in flight" at once. The
      // `user_message` broadcast adopts this entry when it lands, so it stays
      // one bubble — keyed on author, which is why this must be the id the
      // backend will stamp and not a display name like "You".
      // Read the CURRENT value while drawing, so a failure below can put it back.
      // `drawOwnAsk` sets `streaming`, so after this the log can no longer say
      // whether a turn was already running when this send started.
      let wasStreaming = false;
      setLog((prev) => {
        wasStreaming = prev.streaming;
        return drawOwnAsk(prev, { author: currentUser, content: trimmed });
      });
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
        // The send awaits ITS OWN turn, so by the time it resolves that turn has
        // ended (or been detached at the deadline). Re-reading here is the safety
        // net for everything the subscription could have missed — most of all the
        // window on a brand-new chat, where the stream is attached microseconds
        // before the first broadcast and a miss would leave `streaming` true with
        // nothing ever to clear it. Reconcile keeps whatever the screen has that
        // the snapshot does not.
        //
        // Its OWN try, because the send it follows already SUCCEEDED. Letting a
        // failed re-read fall into the catch below would report a turn failure
        // that did not happen — and retract a question the backend has accepted
        // and is going to answer.
        try {
          const fresh = await client.getChat(id);
          qc.setQueryData(qk.kb.chat(id), fresh);
          // Only when the turn actually ENDED. The route awaits its own turn
          // just to `send_await_timeout` and then DETACHES it, so at 25s into a
          // slow turn this read lands mid-flight — and a snapshot that ties on
          // length wins `reconcileSnapshot` and nulls the ephemerals with it:
          // 「請求過於頻繁,N 秒後自動重試」,「整理較早的對話」,「還原工作區 n/m」.
          // Those notices exist to explain exactly the silence being had.
          const last = fresh.messages[fresh.messages.length - 1];
          if (last !== undefined && last.role === "assistant") reconcile(fresh);
        } catch {
          // Best effort. The subscription's terminal reconcile is the other
          // route to the same snapshot, so this is not the last chance.
        }
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        // Take the drawn question back: the send was refused, so no broadcast
        // will ever adopt that entry and no turn is coming for it. Left drawn it
        // would sit there with the error beside it saying it was never sent.
        setLog((prev) => ({
          ...prev,
          // …but do NOT claim the turn ended. This send may have been queued
          // behind one that is still writing, and saying "not streaming" there
          // hides a running turn — the spinner and Stop vanish while the answer
          // is still arriving.
          //
          // What it was BEFORE this send, not "has an answer started": the gap
          // between a turn being accepted and its first token is seconds on a
          // local model, and a failure inside that window would read as nothing
          // running at all.
          streaming: wasStreaming,
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
      reconcile,
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
