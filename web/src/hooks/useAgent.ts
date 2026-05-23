import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api";
import {
  EMPTY_LOG,
  type AgentLog,
  logFromMessages,
  reduceAgent,
} from "../pages/investigation/agentLog";

/**
 * Owns the agent conversation for a single investigation:
 *  - hydrates the persisted Conversation on mount
 *  - streams replies via SSE, folding events into an AgentLog
 *  - exposes send/cancel
 *
 * Pure render-from-state — components subscribe to `log` and pick the
 * pieces they need to draw.
 */
export function useAgent(investigationId: string) {
  const [log, setLog] = useState<AgentLog>(EMPTY_LOG);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let mounted = true;
    setLog(EMPTY_LOG);
    api
      .getConversation(investigationId)
      .then((conv) => {
        if (!mounted) return;
        setLog(conv ? logFromMessages(conv.messages) : EMPTY_LOG);
      })
      .catch(() => {
        // No persisted conversation yet — start fresh.
        if (mounted) setLog(EMPTY_LOG);
      });
    return () => {
      mounted = false;
      abortRef.current?.abort();
    };
  }, [investigationId]);

  const send = useCallback(
    async (content: string) => {
      const trimmed = content.trim();
      if (!trimmed) return;

      // Optimistically append the user message.
      setLog((prev) => ({
        ...prev,
        streaming: true,
        error: null,
        entries: [
          ...prev.entries,
          {
            kind: "message",
            message: { role: "user", author: "default-user", content: trimmed },
          },
        ],
      }));

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        for await (const ev of api.streamAgentEvents({
          investigationId,
          content: trimmed,
          signal: controller.signal,
        })) {
          setLog((prev) => reduceAgent(prev, ev));
        }
      } catch (err: unknown) {
        if ((err as { name?: string } | null)?.name === "AbortError") return;
        const msg = err instanceof Error ? err.message : String(err);
        setLog((prev) => ({ ...prev, streaming: false, error: msg }));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [investigationId],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { log, send, cancel };
}
