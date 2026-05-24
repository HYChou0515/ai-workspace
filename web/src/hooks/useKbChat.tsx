import { useCallback, useEffect, useRef, useState } from "react";

import { kbApi, type KbApi, type KbChatMessage } from "../api/kb";

/**
 * Drives one KB chat thread: hydrate history, stream a turn, expose send.
 *
 * Streaming contract (see api/kb.ts): the SSE carries only the live answer
 * text; the backend resolves `[n]` citations at persist time. So we render
 * deltas optimistically for responsiveness, then refetch the thread on done to
 * swap in the persisted messages (which carry citations).
 *
 * `client` is injectable so the hook is unit-testable against the mock.
 */
export type UseKbChat = {
  chatId: string | null;
  messages: KbChatMessage[];
  streaming: boolean;
  error: string | null;
  send: (content: string) => Promise<void>;
  reset: () => void;
};

export function useKbChat({
  collectionIds,
  chatId: initialChatId = null,
  client = kbApi,
}: {
  collectionIds: string[];
  chatId?: string | null;
  client?: KbApi;
}): UseKbChat {
  const [chatId, setChatId] = useState<string | null>(initialChatId);
  const [messages, setMessages] = useState<KbChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let mounted = true;
    setChatId(initialChatId);
    if (initialChatId == null) {
      setMessages([]);
      return;
    }
    client
      .getChat(initialChatId)
      .then((c) => mounted && setMessages(c.messages))
      .catch(() => mounted && setMessages([]));
    return () => {
      mounted = false;
      abortRef.current?.abort();
    };
  }, [initialChatId, client]);

  const send = useCallback(
    async (content: string) => {
      const trimmed = content.trim();
      if (!trimmed || streaming) return;

      let id = chatId;
      if (id == null) {
        id = (await client.createChat("", collectionIds)).resource_id;
        setChatId(id);
      }

      setError(null);
      setStreaming(true);
      setMessages((prev) => [...prev, userMessage(trimmed), assistantPlaceholder()]);

      const controller = new AbortController();
      abortRef.current = controller;
      try {
        for await (const ev of client.streamMessage({
          chatId: id,
          content: trimmed,
          signal: controller.signal,
        })) {
          if (ev.type === "message_delta") {
            const { text, reasoning } = ev;
            setMessages((prev) => appendToLast(prev, text, reasoning ?? false));
          } else if (ev.type === "error") {
            setError(ev.message);
          }
        }
        // Swap the optimistic tail for the persisted messages (with citations).
        const fresh = await client.getChat(id);
        setMessages(fresh.messages);
      } catch (err: unknown) {
        if ((err as { name?: string } | null)?.name === "AbortError") return;
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setStreaming(false);
      }
    },
    [chatId, collectionIds, client, streaming],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setChatId(null);
    setMessages([]);
    setError(null);
    setStreaming(false);
  }, []);

  return { chatId, messages, streaming, error, send, reset };
}

function userMessage(content: string): KbChatMessage {
  return blank("user", content);
}

function assistantPlaceholder(): KbChatMessage {
  return blank("assistant", "");
}

function blank(role: KbChatMessage["role"], content: string): KbChatMessage {
  return {
    role,
    content,
    reasoning: null,
    tool_name: null,
    tool_args: null,
    tool_call_id: null,
    created_at: Date.now(),
    citations: [],
  };
}

function appendToLast(prev: KbChatMessage[], text: string, reasoning: boolean): KbChatMessage[] {
  const out = prev.slice();
  const last = out[out.length - 1];
  if (!last || last.role !== "assistant") return prev;
  out[out.length - 1] = reasoning
    ? { ...last, reasoning: (last.reasoning ?? "") + text }
    : { ...last, content: last.content + text };
  return out;
}
