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
  const [log, setLog] = useState<AgentLog>(EMPTY_LOG);

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
  }, [threadKey]);

  // Seed from the persisted thread, once per thread.
  useEffect(() => {
    if (threadKey == null || hydrated === undefined || hydratedFor.current === threadKey) return;
    hydratedFor.current = threadKey;
    snapshot(hydrated);
  }, [hydrated, threadKey, snapshot]);

  return { log, setLog, snapshot, reconcile };
}
