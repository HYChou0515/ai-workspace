import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { itemChatApi, type ItemChatApi } from "../api/itemChats";
import { qk } from "../api/queryKeys";
import { isTerminal } from "../events";
import {
  getKbWiki,
  getStored as getKbEnhancementSelection,
  toBodyEnhancements,
  withWikiFlag,
} from "../lib/kbEnhancementMode";
import { getReasoningEffort } from "../lib/reasoningEffort";
import {
  EMPTY_LOG,
  type AgentLog,
  logFromMessages,
  reduceAgent,
} from "../pages/investigation/agentLog";
import type { AgentState } from "./useAgent";
import { useCurrentUser } from "./useCurrentUser";
import { STORE_POLL_MS, useStorePollFallback } from "./useStorePollFallback";

/**
 * Drives ONE chat of an item (topic-hub §3) — a free chat or a workflow chat —
 * reusing the RCA agent-log machinery (foldable reasoning, tool cards, metrics).
 *
 * Same #43 broadcast shape as `useAgent`, keyed on a chat id: a long-lived
 * per-chat subscription drives the log; `send` POSTs to enqueue (events arrive on
 * the stream); on a terminal event the persisted thread is re-snapshotted (it
 * carries resolved `[n]` citations the stream doesn't). `client` is injectable so
 * the hook is unit-testable against a fake.
 *
 * Returns the same shape as `useAgent` (`AgentState`) so the full RCA `AgentPanel`
 * can render against a chat — model picker, suggestions, @mention, attach, undo
 * and Cmd-Enter all work per chat. `investigationId` is the item id (the file /
 * attach / replay APIs are item-scoped, shared across the item's chats).
 */
export type UseItemChat = AgentState & { chatId: string };

export function useItemChat({
  slug,
  itemId,
  chatId,
  client = itemChatApi,
  pollMs = STORE_POLL_MS,
}: {
  slug: string;
  itemId: string;
  chatId: string;
  client?: ItemChatApi;
  /** Store-poll fallback cadence (#202); overridable in tests. */
  pollMs?: number;
}): UseItemChat {
  const qc = useQueryClient();
  const currentUser = useCurrentUser();
  const [log, setLog] = useState<AgentLog>(EMPTY_LOG);
  // Epoch ms of the last live SSE event (or send) — drives the #202 store-poll
  // gate so a healthy same-pod stream is never polled over.
  const lastEventAtRef = useRef(0);

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
          // A live event means this viewer IS on the turn's pod — record it so
          // the #202 store-poll fallback stays dormant while the stream flows.
          lastEventAtRef.current = Date.now();
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

  // #202 cross-pod safety net: when this viewer's /stream is on a pod that isn't
  // running the turn, the broadcast yields nothing and the composer would stay
  // stuck on "working…" forever. While streaming AND the stream is silent, poll
  // the persisted thread (shared store, any pod serves it): surface the user's
  // own just-sent message, and clear "streaming" once the reply is persisted —
  // never regressing a log the live stream may already have advanced.
  useStorePollFallback({
    active: log.streaming,
    isLive: () => Date.now() - lastEventAtRef.current < pollMs,
    fetchThread: () => client.getChat(slug, itemId, chatId),
    onSnapshot: (chat) => {
      const msgs = chat.messages;
      const last = msgs[msgs.length - 1];
      const done = last !== undefined && last.role !== "user";
      if (done) {
        qc.setQueryData(qk.itemChat(slug, itemId, chatId), chat);
        setLog(logFromMessages(msgs));
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
    async (content: string) => {
      const trimmed = content.trim();
      if (!trimmed) return;
      // Flip into "streaming" eagerly so the composer locks; the user message +
      // turn events arrive via the per-chat broadcast (don't push them here).
      // Stamp activity now so the #202 poll gives the live stream one cycle to
      // start before it starts polling the store.
      lastEventAtRef.current = Date.now();
      setLog((prev) => ({ ...prev, streaming: true, error: null, metrics: null }));
      try {
        await client.sendMessage({
          slug,
          itemId,
          chatId,
          content: trimmed,
          reasoningEffort: getReasoningEffort() ?? undefined,
          // Knowledge-search depth + the "Search the wiki" toggle → this turn's
          // ask_knowledge_base lookups (same sticky selection useAgent sends).
          enhancements: withWikiFlag(toBodyEnhancements(getKbEnhancementSelection()), getKbWiki()),
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

  const undo = useCallback(
    async (turns: number) => {
      // #38: drop the last `turns` whole turns server-side (chat-scoped), then
      // re-snapshot the thread (same hydration the post-send tail uses). Files
      // aren't reverted — the AgentPanel confirm copy says so.
      if (turns <= 0) return;
      await client.undoTurns(slug, itemId, chatId, turns);
      const fresh = await client.getChat(slug, itemId, chatId);
      qc.setQueryData(qk.itemChat(slug, itemId, chatId), fresh);
      setLog(logFromMessages(fresh.messages));
    },
    [slug, itemId, chatId, client, qc],
  );

  const mention = useCallback(
    async (userIds: string[], note: string) => {
      if (userIds.length === 0) return;
      await client.mention(slug, itemId, userIds, note);
      // Optimistic: a mention is its own log entry (not an agent turn).
      setLog((prev) => ({
        ...prev,
        entries: [
          ...prev.entries,
          { kind: "mention", by: currentUser, users: userIds, note, at: Date.now() },
        ],
      }));
    },
    [slug, itemId, client, currentUser],
  );

  return { investigationId: itemId, chatId, log, send, mention, cancel, undo };
}
