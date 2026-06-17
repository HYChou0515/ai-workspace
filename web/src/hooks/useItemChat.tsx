import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { itemChatApi, type ItemChatApi } from "../api/itemChats";
import { qk } from "../api/queryKeys";
import { isTerminal } from "../events";
import { getReasoningEffort } from "../lib/reasoningEffort";
import {
  EMPTY_LOG,
  type AgentLog,
  logFromMessages,
  reduceAgent,
} from "../pages/investigation/agentLog";

/**
 * Drives ONE chat of an item (topic-hub §3) — a free chat or a workflow chat —
 * reusing the RCA agent-log machinery (foldable reasoning, tool cards, metrics).
 *
 * Same #43 broadcast shape as `useAgent`, keyed on a chat id: a long-lived
 * per-chat subscription drives the log; `send` POSTs to enqueue (events arrive on
 * the stream); on a terminal event the persisted thread is re-snapshotted (it
 * carries resolved `[n]` citations the stream doesn't). `client` is injectable so
 * the hook is unit-testable against a fake.
 */
export type UseItemChat = {
  chatId: string;
  log: AgentLog;
  send: (content: string) => Promise<void>;
  cancel: () => void;
};

export function useItemChat({
  slug,
  itemId,
  chatId,
  client = itemChatApi,
}: {
  slug: string;
  itemId: string;
  chatId: string;
  client?: ItemChatApi;
}): UseItemChat {
  const qc = useQueryClient();
  const [log, setLog] = useState<AgentLog>(EMPTY_LOG);

  // Hydrate the chat's persisted thread (staleTime 0 so each mount sees the turns
  // the backend persisted after the last stream).
  const { data: hydrated } = useQuery({
    queryKey: qk.itemChat(slug, itemId, chatId),
    queryFn: () => client.getChat(slug, itemId, chatId),
    staleTime: 0,
  });

  // Reset the log when switching chats; the subscription effect tears its own
  // controller down via cleanup, keyed on the same id.
  const hydratedFor = useRef<string | null>(null);
  useEffect(() => {
    hydratedFor.current = null;
    setLog(EMPTY_LOG);
  }, [chatId]);

  useEffect(() => {
    if (hydrated === undefined || hydratedFor.current === chatId) return;
    hydratedFor.current = chatId;
    setLog(logFromMessages(hydrated.messages));
  }, [hydrated, chatId]);

  // Long-lived per-chat broadcast subscription (mirrors useAgent #43): every
  // viewer of this chat sees its turns live (whoever sent them).
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        for await (const ev of client.subscribe(slug, itemId, chatId, controller.signal)) {
          if (ev.type === "file_changed") {
            qc.invalidateQueries({ queryKey: qk.files(itemId) });
            continue;
          }
          setLog((prev) => reduceAgent(prev, ev));
          if (isTerminal(ev)) {
            const fresh = await client.getChat(slug, itemId, chatId);
            qc.setQueryData(qk.itemChat(slug, itemId, chatId), fresh);
            setLog(logFromMessages(fresh.messages));
          }
        }
      } catch (err: unknown) {
        if ((err as { name?: string } | null)?.name === "AbortError") return;
      }
    })();
    return () => controller.abort();
  }, [slug, itemId, chatId, client, qc]);

  const send = useCallback(
    async (content: string) => {
      const trimmed = content.trim();
      if (!trimmed) return;
      // Flip into "streaming" eagerly so the composer locks; the user message +
      // turn events arrive via the per-chat broadcast (don't push them here).
      setLog((prev) => ({ ...prev, streaming: true, error: null, metrics: null }));
      try {
        await client.sendMessage({
          slug,
          itemId,
          chatId,
          content: trimmed,
          reasoningEffort: getReasoningEffort() ?? undefined,
        });
      } catch (err: unknown) {
        if ((err as { name?: string } | null)?.name === "AbortError") return;
        const msg = err instanceof Error ? err.message : String(err);
        setLog((prev) => ({ ...prev, streaming: false, error: msg }));
      }
    },
    [slug, itemId, chatId, client],
  );

  const cancel = useCallback(() => {
    // The turn runs server-side over the broadcast — no local fetch to abort.
    // Tell the BE to tear the in-flight turn down, and flip out of "streaming"
    // immediately (#49) so Stop unblocks the composer even if teardown lags.
    void client.cancelMessage(slug, itemId, chatId);
    setLog((prev) => ({ ...prev, streaming: false }));
  }, [slug, itemId, chatId, client]);

  return { chatId, log, send, cancel };
}
