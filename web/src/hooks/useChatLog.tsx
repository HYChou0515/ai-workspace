import { useQuery, type QueryKey } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Message } from "../api/types";
import {
  EMPTY_LOG,
  type AgentLog,
  logFromMessages,
  reconcileSnapshot,
} from "../pages/investigation/agentLog";

/**
 * The chat log itself: its state, its hydration from the persisted thread, and
 * the snapshot that replaces it.
 *
 * The layer below {@link useChatSession}, shared by BOTH chat transports —
 * the broadcast chats (an item's default chat + its named chats) and the KB
 * chat, whose POST *is* its stream. Those transports genuinely differ; this
 * doesn't, and it was written three times.
 *
 * It is also the one place a snapshot can overwrite what the user is reading, so
 * keeping it single makes that behaviour changeable in one edit rather than three.
 */

export type ChatThread = { messages: readonly Message[] };

export type ChatLogState = {
  log: AgentLog;
  setLog: React.Dispatch<React.SetStateAction<AgentLog>>;
  /** Replace the log with the persisted thread (`null` → empty). Authoritative —
   * use only where a SMALLER thread is the point (initial hydration, undo). */
  snapshot: (thread: ChatThread | null | undefined) => void;
  /** Fold the persisted thread in without deleting live-only content — the
   * streamed-but-unpersisted answer, the turn error, stream-only banners. Every
   * MID-TURN re-hydrate (terminal event, reconnect, store-poll) uses this. */
  reconcile: (thread: ChatThread | null | undefined) => void;
};

export function useChatLog({
  threadKey,
  queryKey,
  getThread,
}: {
  /** Identity of the thread; `null` = none yet (a KB chat before its first
   * send), which skips hydration and leaves the log empty. */
  threadKey: string | null;
  queryKey: QueryKey;
  getThread: () => Promise<ChatThread | null>;
}): ChatLogState {
  const [log, setLogState] = useState<AgentLog>(EMPTY_LOG);

  // Every write to `log` is guarded HERE, where the state is made, rather than
  // at each caller. Both transports write it from async paths that can outlive
  // the view — a stream that ends, a snapshot that arrives — and a caller-side
  // guard is one per exit: `useKbChat.send` alone reaches six state writes after
  // an await, five of them log writes, and the guard it had was on the one that
  // fires LAST, so it moved which line threw and removed nothing.
  //
  // In a real browser a set-state-after-unmount is merely pointless. In a
  // torn-down test environment it throws `ReferenceError: window is not
  // defined` out of React and reddens whichever FILE happened to be running.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const setLog = useCallback<React.Dispatch<React.SetStateAction<AgentLog>>>((update) => {
    if (mounted.current) setLogState(update);
  }, []);

  // staleTime 0 so each mount sees the turns the backend persisted after the
  // last stream.
  const { data: hydrated } = useQuery({
    queryKey,
    queryFn: getThread,
    enabled: threadKey != null,
    staleTime: 0,
  });

  const snapshot = useCallback((thread: ChatThread | null | undefined) => {
    setLog(thread ? logFromMessages(thread.messages) : EMPTY_LOG);
  }, []);

  const reconcile = useCallback((thread: ChatThread | null | undefined) => {
    if (!thread) return; // nothing persisted yet — never blank the screen for that
    setLog((prev) => reconcileSnapshot(prev, thread));
  }, []);

  // Clear on a thread switch so one thread's messages never linger under
  // another's while the new one hydrates. With `threadKey === null` there is no
  // query, so this is also what leaves a brand-new chat empty.
  const hydratedFor = useRef<string | null>(null);
  useEffect(() => {
    hydratedFor.current = null;
    setLog(EMPTY_LOG);
  }, [threadKey, setLog]);

  // Seed from the persisted thread, once per thread.
  //
  // The subscription starts at mount and this query resolves later, so events
  // can already have arrived. Seeding with the authoritative replace would then
  // delete them — the same "the answer vanished" failure as any other
  // re-hydrate, just racing at mount instead of mid-turn. Reconcile whenever
  // there is anything on screen to protect.
  useEffect(() => {
    if (threadKey == null || hydrated === undefined || hydratedFor.current === threadKey) return;
    hydratedFor.current = threadKey;
    setLog((prev) =>
      prev.entries.length === 0
        ? hydrated
          ? logFromMessages(hydrated.messages)
          : EMPTY_LOG
        : reconcileSnapshot(prev, hydrated ?? { messages: [] }),
    );
  }, [hydrated, threadKey, setLog]);

  return { log, setLog, snapshot, reconcile };
}
