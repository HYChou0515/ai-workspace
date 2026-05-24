import { useCallback, useEffect, useRef, useState } from "react";

import { kbApi, type KbApi } from "../api/kb";
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
  const [chatId, setChatId] = useState<string | null>(initialChatId);
  const [log, setLog] = useState<AgentLog>(EMPTY_LOG);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let mounted = true;
    setChatId(initialChatId);
    if (initialChatId == null) {
      setLog(EMPTY_LOG);
      return;
    }
    client
      .getChat(initialChatId)
      .then((c) => mounted && setLog(logFromMessages(c.messages)))
      .catch(() => mounted && setLog(EMPTY_LOG));
    return () => {
      mounted = false;
      abortRef.current?.abort();
    };
  }, [initialChatId, client]);

  const send = useCallback(
    async (content: string) => {
      const trimmed = content.trim();
      if (!trimmed || log.streaming) return;

      let id = chatId;
      if (id == null) {
        id = (await client.createChat("", collectionIds)).resource_id;
        setChatId(id);
        onChatCreated?.(id);
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
        })) {
          setLog((prev) => reduceAgent(prev, ev));
        }
        // Snapshot the persisted thread — it carries the resolved [n] citations.
        const fresh = await client.getChat(id);
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
    [chatId, collectionIds, client, log.streaming, onChatCreated],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setChatId(null);
    setLog(EMPTY_LOG);
  }, []);

  return { chatId, log, send, reset };
}
