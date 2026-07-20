import { useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";

import type { AgentEvent } from "../events";
import { isTerminal } from "../events";
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

export type ChatSendOpts = { applySkills?: string[]; imagePaths?: string[] };

export type BroadcastChatTransport = {
  /** Identity of the thread. A change resets the log and re-subscribes. */
  threadKey: string;
  /** react-query key holding the persisted thread. */
  queryKey: QueryKey;
  /** react-query key to invalidate when a `file_changed` event arrives. */
  filesKey: QueryKey;
  /** Read the persisted thread. `null` = no thread yet. */
  getThread: () => Promise<ChatThread | null>;
  /** The long-lived broadcast subscription. */
  subscribe: (signal: AbortSignal) => AsyncIterable<AgentEvent>;
  /** Enqueue a turn (202; the events arrive on the subscription). */
  post: (content: string, opts?: ChatSendOpts) => Promise<void>;
  /** Tell the backend to tear down the in-flight turn. */
  requestCancel: () => void;
  undoTurns: (turns: number) => Promise<void>;
  addMention: (userIds: string[], note: string) => Promise<void>;
};

export type ChatSession = {
  log: AgentLog;
  send: (content: string, opts?: ChatSendOpts) => Promise<void>;
  mention: (userIds: string[], note: string) => Promise<void>;
  cancel: () => void;
  undo: (turns: number) => Promise<void>;
};

/** Gateway/timeout statuses (and a bare network drop, 0) that mean "the request
 * was cut, but the turn may well be running" — never "the turn failed". */
const GATEWAY_CUT = new Set([0, 502, 503, 504]);

const isAbort = (err: unknown) => (err as { name?: string } | null)?.name === "AbortError";

export function useChatSession(
  transport: BroadcastChatTransport,
  pollMs: number = STORE_POLL_MS,
): ChatSession {
  const qc = useQueryClient();
  const currentUser = useCurrentUser();
  // Epoch ms of the last live event (or send) — gates the #202 store-poll so a
  // healthy same-pod stream is never polled over.
  const lastEventAtRef = useRef(0);
  const { log, setLog, snapshot } = useChatLog({
    threadKey: transport.threadKey,
    queryKey: transport.queryKey,
    getThread: transport.getThread,
  });

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
      while (!stopped) {
        try {
          for await (const ev of transport.subscribe(controller.signal)) {
            backoff = 1000; // a healthy stream resets the backoff
            // A live event means this viewer IS on the turn's pod — record it so
            // the #202 store-poll stays dormant while the stream flows.
            lastEventAtRef.current = Date.now();
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
                snapshot(fresh);
              }
            }
          }
        } catch (err: unknown) {
          if (isAbort(err)) return; // unmount / thread switch
          // Anything else → fall through to the reconnect delay.
        }
        if (stopped) return;
        await sleep(backoff);
        if (stopped) return;
        const fresh = await transport.getThread().catch(() => null);
        if (fresh && !stopped) {
          qc.setQueryData(transport.queryKey, fresh);
          snapshot(fresh);
        }
        backoff = Math.min(backoff * 2, 15000);
      }
    })();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [transport, qc, snapshot]);

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
        snapshot(thread);
        return;
      }
      const snap = logFromMessages(msgs);
      setLog((prev) =>
        snap.entries.length > prev.entries.length ? { ...snap, streaming: true } : prev,
      );
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
    transport.requestCancel();
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

  return { log, send, mention, cancel, undo };
}
