import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { kbApi, type KbApi } from "../api/kb";
import { qk } from "../api/queryKeys";
import { getKbAgentName } from "../lib/kbAgent";
import {
  getStored as getKbEnhancementSelection,
  toBodyEnhancements,
} from "../lib/kbEnhancementMode";
import { getReasoningEffort } from "../lib/reasoningEffort";
import { getKbSearchMax } from "../lib/kbSearchMax";
import { getKbWikiMax } from "../lib/kbWikiMax";
import {
  EMPTY_LOG,
  type AgentLog,
  logFromMessages,
  reduceAgent,
} from "../pages/investigation/agentLog";

/**
 * Drives one KB chat thread, reusing the RCA agent-log machinery so the KB chat
 * renders identically (foldable reasoning, tool-call cards, live token metrics).
 *
 * The SSE stream is folded through `reduceAgent` for live progress; on `done`
 * we refetch the thread and snapshot it via `logFromMessages` — that persisted
 * view carries the resolved `[n]` citations (the stream doesn't).
 *
 * `client` is injectable so the hook is unit-testable against the mock.
 */
export type UseKbChat = {
  chatId: string | null;
  log: AgentLog;
  send: (content: string) => Promise<void>;
  cancel: () => void;
  reset: () => void;
};

export function useKbChat({
  collectionIds,
  chatId: initialChatId = null,
  client = kbApi,
  onChatCreated,
}: {
  collectionIds: string[];
  chatId?: string | null;
  client?: KbApi;
  /** Fired when the first message creates the thread (so the list can refresh). */
  onChatCreated?: (chatId: string) => void;
}): UseKbChat {
  const qc = useQueryClient();
  const [chatId, setChatId] = useState<string | null>(initialChatId);
  const [log, setLog] = useState<AgentLog>(EMPTY_LOG);
  const abortRef = useRef<AbortController | null>(null);

  // Hydrate an existing thread (shares the cache with KbChatView's title
  // query). A new thread (initialChatId == null) builds its log from the stream.
  const { data: hydrated } = useQuery({
    queryKey: qk.kb.chat(initialChatId ?? "__new__"),
    queryFn: () => client.getChat(initialChatId as string),
    enabled: initialChatId != null,
    staleTime: 0,
  });

  // Reset + abort the running stream when the mounted thread changes.
  const hydratedFor = useRef<string | null>(null);
  useEffect(() => {
    setChatId(initialChatId);
    hydratedFor.current = null;
    if (initialChatId == null) setLog(EMPTY_LOG);
    return () => abortRef.current?.abort();
  }, [initialChatId]);

  // Seed the log from the hydrated thread, once per thread id.
  useEffect(() => {
    if (initialChatId == null || hydrated === undefined) return;
    if (hydratedFor.current === initialChatId) return;
    hydratedFor.current = initialChatId;
    setLog(logFromMessages(hydrated.messages));
  }, [hydrated, initialChatId]);

  const send = useCallback(
    async (content: string) => {
      const trimmed = content.trim();
      if (!trimmed || log.streaming) return;

      let id = chatId;
      if (id == null) {
        id = (await client.createChat("", collectionIds)).resource_id;
        setChatId(id);
        onChatCreated?.(id);
        void qc.invalidateQueries({ queryKey: qk.kb.chats });
      }

      setLog((prev) => ({
        ...prev,
        streaming: true,
        error: null,
        metrics: null,
        entries: [
          ...prev.entries,
          { kind: "message", at: Date.now(), message: { role: "user", content: trimmed, author: "You" } },
        ],
      }));

      const controller = new AbortController();
      abortRef.current = controller;
      try {
        for await (const ev of client.streamMessage({
          chatId: id,
          content: trimmed,
          signal: controller.signal,
          reasoningEffort: getReasoningEffort() ?? undefined,
          enhancements: toBodyEnhancements(getKbEnhancementSelection()),
          agentName: getKbAgentName() ?? undefined,
          // #334: per-message cap on this reply's kb_search calls (0 = no search).
          maxKbSearches: getKbSearchMax(),
          // #506: per-message cap on this reply's wiki greps (replaces the toggle).
          maxWikiSearches: getKbWikiMax(),
        })) {
          setLog((prev) => reduceAgent(prev, ev));
        }
        // Snapshot the persisted thread — it carries the resolved [n] citations.
        // Push it into the cache too so KbChatView's title query stays in sync.
        const fresh = await client.getChat(id);
        qc.setQueryData(qk.kb.chat(id), fresh);
        setLog(logFromMessages(fresh.messages));
      } catch (err: unknown) {
        if ((err as { name?: string } | null)?.name === "AbortError") return;
        const msg = err instanceof Error ? err.message : String(err);
        setLog((prev) => ({ ...prev, streaming: false, error: msg }));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setLog((prev) => ({ ...prev, streaming: false }));
      }
    },
    [chatId, collectionIds, client, log.streaming, onChatCreated, qc],
  );

  const cancel = useCallback(() => {
    // Abort the local stream AND tell the BE to tear the turn down (mirrors
    // useAgent.cancel) — closing the socket alone doesn't always reach the
    // runner. Only an already-created thread has a server turn to cancel.
    abortRef.current?.abort();
    if (chatId) void client.cancelMessage(chatId);
    // #49: flip out of "streaming" immediately so Stop unblocks the
    // composer even if the stream is slow to tear down.
    setLog((prev) => ({ ...prev, streaming: false }));
  }, [chatId, client]);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setChatId(null);
    setLog(EMPTY_LOG);
  }, []);

  return { chatId, log, send, cancel, reset };
}
