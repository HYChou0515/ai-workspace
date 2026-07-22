import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { kbApi, type KbApi, type KbImageInput } from "../api/kb";
import { qk } from "../api/queryKeys";
import { getKbAgentName } from "../lib/kbAgent";
import {
  getStored as getKbEnhancementSelection,
  toBodyEnhancements,
} from "../lib/kbEnhancementMode";
import { getReasoningEffort } from "../lib/reasoningEffort";
import { getKbDisclosure } from "../lib/kbDisclosure";
import { getKbSearchMax } from "../lib/kbSearchMax";
import { getKbWikiMax } from "../lib/kbWikiMax";
import { EMPTY_LOG, type AgentLog, reduceAgent } from "../pages/investigation/agentLog";
import { useChatLog } from "./useChatLog";

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
  const abortRef = useRef<AbortController | null>(null);

  // Log state + hydration are shared with the broadcast chats (useChatLog); only
  // the TRANSPORT below differs — here the POST *is* the stream, so there is no
  // separate subscription (and therefore no reconnect / cross-pod poll to run).
  const { log, setLog, reconcile } = useChatLog({
    threadKey: initialChatId,
    // Shares the cache with KbChatView's title query.
    queryKey: qk.kb.chat(initialChatId ?? "__new__"),
    getThread: () => client.getChat(initialChatId as string),
  });

  // Abort the running stream when the mounted thread changes.
  useEffect(() => {
    setChatId(initialChatId);
    return () => abortRef.current?.abort();
  }, [initialChatId]);

  const send = useCallback(
    async (content: string, image?: KbImageInput) => {
      const trimmed = content.trim();
      // #513 P10: an image-only message (no text) is a valid turn — the VLM
      // description carries the query — so gate on text OR image.
      if ((!trimmed && !image) || log.streaming) return;

      let id = chatId;
      if (id == null) {
        id = (await client.createChat("", collectionIds, excludedCollectionIds)).resource_id;
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
          // #513 P10: the server VLM-describes this transient image into the query.
          image,
          signal: controller.signal,
          reasoningEffort: getReasoningEffort() ?? undefined,
          enhancements: toBodyEnhancements(getKbEnhancementSelection()),
          agentName: getKbAgentName() ?? undefined,
          // #334: per-message cap on this reply's kb_search calls (0 = no search).
          maxKbSearches: getKbSearchMax(),
          disclosure: getKbDisclosure(),
          // #506: per-message cap on this reply's wiki greps (replaces the toggle).
          maxWikiSearches: getKbWikiMax(),
        })) {
          setLog((prev) => reduceAgent(prev, ev));
        }
        // Snapshot the persisted thread — it carries the resolved [n] citations.
        // Push it into the cache too so KbChatView's title query stays in sync.
        const fresh = await client.getChat(id);
        qc.setQueryData(qk.kb.chat(id), fresh);
        // Reconcile, not replace: the persisted thread is what carries the
        // resolved [n] citations, but it must never cost the user the answer
        // they just watched stream in.
        reconcile(fresh);
      } catch (err: unknown) {
        if ((err as { name?: string } | null)?.name === "AbortError") return;
        const msg = err instanceof Error ? err.message : String(err);
        setLog((prev) => ({ ...prev, streaming: false, error: msg }));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setLog((prev) => ({ ...prev, streaming: false }));
      }
    },
    [chatId, collectionIds, excludedCollectionIds, client, log.streaming, onChatCreated, qc, setLog, reconcile],
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
