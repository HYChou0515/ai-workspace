import { useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import type { AgentEvent } from "../events";
import { eventSeq, isTerminal } from "../events";
import { type AgentLog, logFromMessages, reduceAgent } from "../pages/investigation/agentLog";
import { type ChatThread, useChatLog } from "./useChatLog";
import { useCurrentUser } from "./useCurrentUser";
import { STORE_POLL_MS, useStorePollFallback } from "./useStorePollFallback";

export { STORE_POLL_MS };
export type { ChatThread };

/**
 * The chat-turn state machine for a #43 BROADCAST chat, written once.
 *
 * `useAgent` (an item's default chat) and `useItemChat` (a named chat) were
 * line-for-line the same machine over two different clients, and they drifted:
 * the #493 auto-reconnect and gateway tolerance landed in one and never crossed
 * to the other, so a WorkItem chat went permanently deaf on a single dropped
 * stream. Both now supply a {@link BroadcastChatTransport} and share this body,
 * so a fix can no longer reach one surface and miss the other.
 *
 * Broadcast semantics (#43): the POST only ENQUEUES the turn — the user's own
 * message and every turn event come back over the shared subscription, so all
 * viewers see the turn, and nothing is pushed optimistically here (that would
 * double it).
 */

export type ChatSendOpts = {
  applySkills?: string[];
  imagePaths?: string[];
  /** grill-me: the `ask_user` question this message answers, when the user
   * clicked an option instead of typing. */
  answers?: string;
};

export type BroadcastChatTransport = {
  /** Identity of the thread. A change resets the log and re-subscribes. */
  threadKey: string;
  /** react-query key holding the persisted thread. */
  queryKey: QueryKey;
  /** react-query key to invalidate when a `file_changed` event arrives. */
  filesKey: QueryKey;
  /** Read the persisted thread. `null` = no thread yet. */
  getThread: () => Promise<ChatThread | null>;
  /** The long-lived broadcast subscription. `since` (passed only on a RECONNECT)
   * asks the server to first replay the events after that broadcast seq — the
   * same-pod gap — before resuming live. */
  subscribe: (signal: AbortSignal, since?: number) => AsyncIterable<AgentEvent>;
  /** Enqueue a turn (202; the events arrive on the subscription). */
  post: (content: string, opts?: ChatSendOpts) => Promise<void>;
  /** Tell the backend to tear down the in-flight turn. Returning the promise
   * lets a FAILED stop be surfaced — the UI has already said it stopped. */
  requestCancel: () => void | Promise<unknown>;
  undoTurns: (turns: number) => Promise<void>;
  addMention: (userIds: string[], note: string) => Promise<void>;
};

/** Whether this viewer is actually receiving live events.
 *
 * Losing the stream used to be completely invisible: the subscription's `catch`
 * swallowed every non-abort error — no banner, no state, not even a
 * `console.error` — so an idle-proxy cut or a pod rollover looked exactly like a
 * chat where nothing was happening, while the answer on screen quietly stopped
 * growing (live events are dropped when nobody is attached; there is no replay).
 *
 * The content is not at risk — the turn is persisted and re-read — so what is
 * left is entirely a matter of TELLING the user. A frozen answer labelled
 * "reconnecting" is a wait; the same frozen answer in silence is a hang they can
 * only read as broken. `attempts` separates a blip from an outage. */
export type ChatConnection = {
  state: "connecting" | "live" | "reconnecting";
  /** Whether a real turn event has actually arrived on this subscription.
   *
   * `subscribe` succeeds on ANY replica: a pod that is not running the turn just
   * creates an empty session for the key and starts heartbeating, so the viewer
   * is subscribed, healthy-looking and completely deaf. "Connected" therefore
   * says nothing about delivery, and asserting it would be a claim we have no
   * evidence for. Presence churn ("someone is typing") does not count — it is
   * not turn progress, and counting it suppressed the cross-pod store-poll in
   * exactly the situation the poll exists for. */
  receiving: boolean;
  /** Why the stream last dropped; null while healthy. */
  error: string | null;
  /** Consecutive failed reconnects (0 while healthy). */
  attempts: number;
};

export type ChatSession = {
  log: AgentLog;
  connection: ChatConnection;
  send: (content: string, opts?: ChatSendOpts) => Promise<void>;
  mention: (userIds: string[], note: string) => Promise<void>;
  cancel: () => void;
  undo: (turns: number) => Promise<void>;
};

/** Gateway/timeout statuses (and a bare network drop, 0) that mean "the request
 * was cut, but the turn may well be running" — never "the turn failed". */
const GATEWAY_CUT = new Set([0, 502, 503, 504]);

const isAbort = (err: unknown) => (err as { name?: string } | null)?.name === "AbortError";

/** The transient "you may have missed a piece" notice shown while a dropped
 * stream reconnects. Removed again if the reconnect's replay turns out contiguous
 * (the same-pod buffer filled the gap), so it only stays for a real hole. */
const GAP_BANNER = "連線中斷,這裡可能少了一段";

export function useChatSession(
  transport: BroadcastChatTransport,
  pollMs: number = STORE_POLL_MS,
): ChatSession {
  const qc = useQueryClient();
  const currentUser = useCurrentUser();
  // Epoch ms of the last live event (or send) — gates the #202 store-poll so a
  // healthy same-pod stream is never polled over.
  const lastEventAtRef = useRef(0);
  // #43 reconnect replay: the highest broadcast seq seen on this subscription so
  // a reconnect can resume from it (`?since=`). Reset when the thread changes so a
  // new thread's seqs (which restart at 1) are tracked, not shadowed by the old.
  const maxSeqRef = useRef(0);
  // Whether a gap banner was added on the last drop and is awaiting confirmation:
  // the reconnect's first event decides if the replay filled the hole (remove) or
  // a real gap remains (keep).
  const gapBannerPendingRef = useRef(false);
  const [connection, setConnection] = useState<ChatConnection>({
    state: "connecting",
    receiving: false,
    error: null,
    attempts: 0,
  });
  const { log, setLog, snapshot, reconcile } = useChatLog({
    threadKey: transport.threadKey,
    queryKey: transport.queryKey,
    getThread: transport.getThread,
  });

  // A new thread restarts the broadcast seq at 1, so forget the old thread's max
  // or a stale, larger value would make every reconnect ask to resume past the
  // new thread's events (replaying nothing) forever.
  useEffect(() => {
    maxSeqRef.current = 0;
  }, [transport.threadKey]);

  // The long-lived broadcast subscription (#43) with the #493 auto-reconnect:
  // the stream can drop mid-turn (an idle ingress cut, a pod rollover). A dropped
  // stream used to be swallowed, leaving the viewer stuck forever. Instead, back
  // off, re-hydrate (so a turn that finished during the gap shows up) and
  // re-subscribe. Only an abort (unmount / thread switch) stops the loop.
  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;
    const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
    void (async () => {
      let backoff = 1000;
      // The FIRST subscribe of this effect is a fresh connect — no replay. Every
      // later one is a RECONNECT and resumes from the last seq we saw, so the
      // events emitted during the gap are replayed on the same pod.
      let firstConnect = true;
      while (!stopped) {
        const since = firstConnect ? undefined : maxSeqRef.current;
        firstConnect = false;
        let firstEventThisConnect = true;
        try {
          // Opening the stream clears the error, but NOT the attempt count: being
          // subscribed is not the same as receiving, and a socket that opens and
          // immediately dies would otherwise reset the counter every cycle and
          // make a sustained outage look like an endless first blip.
          setConnection((c) => (c.state === "live" ? c : { ...c, state: "live", error: null }));
          for await (const ev of transport.subscribe(controller.signal, since)) {
            backoff = 1000; // a healthy stream resets the backoff
            // Track the highest broadcast seq so the next reconnect resumes here.
            const seq = eventSeq(ev);
            if (seq !== undefined && seq > maxSeqRef.current) maxSeqRef.current = seq;
            // On the first event after a reconnect, decide the fate of the gap
            // banner: a contiguous replay (the very next seq) means the same-pod
            // buffer filled the hole, so drop the banner; a jump means a real gap
            // survived it, so keep it.
            if (firstEventThisConnect) {
              firstEventThisConnect = false;
              if (gapBannerPendingRef.current) {
                const contiguous = seq !== undefined && since !== undefined && seq === since + 1;
                if (contiguous) {
                  setLog((prev) => {
                    const last = prev.entries[prev.entries.length - 1];
                    return last?.kind === "banner" && last.text === GAP_BANNER
                      ? { ...prev, entries: prev.entries.slice(0, -1) }
                      : prev;
                  });
                }
                gapBannerPendingRef.current = false;
              }
            }
            // A real TURN event is the proof the stream works — only now is the
            // outage over. Presence is excluded for the same reason as above: a
            // pod that is not running the turn still broadcasts it.
            if (ev.type !== "presence") {
              setConnection((c) =>
                c.state === "live" && c.attempts === 0 && c.receiving
                  ? c
                  : { state: "live", receiving: true, error: null, attempts: 0 },
              );
            }
            // Only a real turn event proves this viewer is on the turn's pod.
            // Presence churn is broadcast by the session regardless, so counting
            // it kept the #202 store-poll dormant in exactly the cross-pod case
            // the poll exists for.
            if (ev.type !== "presence") lastEventAtRef.current = Date.now();
            if (ev.type === "file_changed") {
              // A human edited a workspace file — refetch the tree. Not a turn
              // event, so it never folds into the log.
              void qc.invalidateQueries({ queryKey: transport.filesKey });
              continue;
            }
            setLog((prev) => reduceAgent(prev, ev));
            if (isTerminal(ev)) {
              // Re-snapshot from the store — it carries the BE-attached
              // `ask_knowledge_base` citations the stream doesn't emit.
              const fresh = await transport.getThread();
              if (fresh) {
                qc.setQueryData(transport.queryKey, fresh);
                // The terminal event is published BEFORE the turn is persisted,
                // so this read can legitimately lose the race — reconcile, never
                // replace, or the just-streamed reply is wiped with no later
                // event to put it back.
                reconcile(fresh);
              }
            }
          }
        } catch (err: unknown) {
          if (isAbort(err)) return; // unmount / thread switch
          // Anything else → say so, then fall through to the reconnect delay.
          // Swallowing this is what made a dropped stream indistinguishable from
          // a quiet one.
          const why = err instanceof Error ? err.message : String(err);
          setConnection((c) => ({
            state: "reconnecting",
            receiving: false,
            error: why,
            attempts: c.attempts + 1,
          }));
        }
        // A stream that ENDS without throwing (the server closed it) is also a
        // lost connection, not a finished chat.
        setConnection((c) =>
          c.state === "reconnecting"
            ? c
            : { state: "reconnecting", receiving: false, error: null, attempts: c.attempts + 1 },
        );
        if (stopped) return;
        // Events published while nobody is attached are dropped and never
        // replayed, so an answer that resumes after this gap is missing a piece
        // and rejoins mid-sentence. Splicing the two halves together silently
        // presents a mutilated answer as a whole one — say where the hole is.
        // Gate on "an answer was being written", not on `streaming`: the
        // subscription starts before hydration resolves, so the flag can still
        // be false while text is visibly arriving. A hole only matters where
        // there was something to interrupt.
        setLog((prev) => {
          const last = prev.entries[prev.entries.length - 1];
          const midAnswer = last?.kind === "message" && last.message.role === "assistant";
          return midAnswer
            ? {
                ...prev,
                entries: [...prev.entries, { kind: "banner", at: Date.now(), text: GAP_BANNER }],
              }
            : prev;
        });
        // Let the reconnect's first event confirm whether the replay filled the
        // gap (remove the banner) or a real hole remains (keep it).
        gapBannerPendingRef.current = true;
        await sleep(backoff);
        if (stopped) return;
        const fresh = await transport.getThread().catch(() => null);
        if (fresh && !stopped) {
          qc.setQueryData(transport.queryKey, fresh);
          // A drop mid-turn re-hydrates a thread that does NOT yet contain the
          // answer being streamed — reconcile so reconnecting never costs the
          // user what they were reading.
          reconcile(fresh);
        }
        backoff = Math.min(backoff * 2, 15000);
      }
    })();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [transport, qc, reconcile]);

  // #202 cross-pod safety net: when this viewer's stream is on a pod that isn't
  // running the turn, the broadcast yields nothing and the composer would stay
  // stuck on "working…". While streaming AND the stream is silent, poll the
  // persisted thread (shared store, any pod serves it): surface the user's own
  // just-sent message, and clear "streaming" once the reply is persisted — never
  // regressing a log the live stream may already have advanced.
  useStorePollFallback({
    active: log.streaming,
    isLive: () => Date.now() - lastEventAtRef.current < pollMs,
    fetchThread: () => transport.getThread(),
    onSnapshot: (thread) => {
      if (!thread) return;
      const msgs = thread.messages;
      const last = msgs[msgs.length - 1];
      const done = last !== undefined && last.role !== "user";
      if (done) {
        qc.setQueryData(transport.queryKey, thread);
        reconcile(thread);
        return;
      }
      const snap = logFromMessages(msgs);
      setLog((prev) =>
        snap.entries.length > prev.entries.length ? { ...snap, streaming: true } : prev,
      );
    },
    // The poll IS the safety net for a cross-pod viewer. If it is failing too,
    // there is no live stream AND no fallback — the worst state available, and
    // it used to look identical to "nothing has happened yet".
    onError: (err) => {
      const why = err instanceof Error ? err.message : String(err);
      setConnection((c) => ({
        state: "reconnecting",
        receiving: false,
        error: why,
        attempts: c.attempts + 1,
      }));
    },
    pollMs,
  });

  const send = useCallback(
    async (content: string, opts?: ChatSendOpts) => {
      const trimmed = content.trim();
      if (!trimmed) return;
      // Flip into "streaming" eagerly so the composer locks, but DON'T push the
      // user message — it arrives via the `user_message` broadcast (#43). Stamp
      // activity so the #202 poll gives the live stream one cycle to start.
      lastEventAtRef.current = Date.now();
      setLog((prev) => ({ ...prev, streaming: true, error: null, metrics: null }));
      try {
        await transport.post(trimmed, opts);
      } catch (err: unknown) {
        if (isAbort(err)) return;
        // #493: a gateway cut does NOT mean the turn failed — the POST may have
        // been dropped by an idle proxy while the turn runs server-side. Stay in
        // "streaming" so the stream / store-poll surfaces the result, instead of
        // flashing an error the user has to dismiss while the answer arrives.
        const status = (err as { status?: number } | null)?.status;
        if (status !== undefined && GATEWAY_CUT.has(status)) {
          lastEventAtRef.current = Date.now(); // give the poll a grace cycle
          return;
        }
        const msg = err instanceof Error ? err.message : String(err);
        setLog((prev) => ({ ...prev, streaming: false, error: msg }));
      }
    },
    [transport],
  );

  const cancel = useCallback(() => {
    // The turn runs server-side over the broadcast — there's no local fetch to
    // abort. Tell the BE to tear it down, and flip out of "streaming" right now
    // (#49): teardown can lag on a long exec, and the user pressed Stop.
    // Flip out of "streaming" right now (#49) — the user pressed Stop and
    // teardown can lag on a long exec. But if the request to stop never lands,
    // saying nothing means the turn runs on invisibly and the UI has already
    // told them it stopped.
    void Promise.resolve(transport.requestCancel()).catch((err: unknown) => {
      const why = err instanceof Error ? err.message : String(err);
      setLog((prev) => ({ ...prev, error: `停止失敗,這一輪可能仍在進行:${why}` }));
    });
    setLog((prev) => ({ ...prev, streaming: false }));
  }, [transport]);

  const undo = useCallback(
    async (turns: number) => {
      // #38: drop the last `turns` whole turns server-side, then re-snapshot.
      // Files aren't reverted — the caller's confirm copy says so.
      if (turns <= 0) return;
      await transport.undoTurns(turns);
      const fresh = await transport.getThread();
      qc.setQueryData(transport.queryKey, fresh ?? null);
      snapshot(fresh);
    },
    [transport, qc, snapshot],
  );

  const mention = useCallback(
    async (userIds: string[], note: string) => {
      if (userIds.length === 0) return;
      await transport.addMention(userIds, note);
      // Optimistic: a mention is its own log entry, not an agent turn.
      setLog((prev) => ({
        ...prev,
        entries: [
          ...prev.entries,
          { kind: "mention", by: currentUser, users: userIds, note, at: Date.now() },
        ],
      }));
    },
    [transport, currentUser],
  );

  return { log, connection, send, mention, cancel, undo };
}
