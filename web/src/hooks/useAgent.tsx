import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { api } from "../api";
import {
  EMPTY_LOG,
  type AgentLog,
  logFromMessages,
  reduceAgent,
} from "../pages/investigation/agentLog";

/**
 * Single source of truth for the agent conversation per investigation.
 *
 * Hydrates Conversation on mount, streams replies via SSE, exposes send/cancel.
 * The Provider wraps the workspace so both AgentPanel and the bottom panel
 * (which surfaces agent-log lines) read the same log.
 */

type AgentState = {
  log: AgentLog;
  send: (content: string) => Promise<void>;
  cancel: () => void;
};

const AgentContext = createContext<AgentState | null>(null);

export function useAgentInternal(investigationId: string): AgentState {
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

      setLog((prev) => ({
        ...prev,
        streaming: true,
        error: null,
        metrics: null, // fresh telemetry for the new turn
        entries: [
          ...prev.entries,
          {
            kind: "message",
            at: Date.now(),
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
    // Abort the local fetch (closes the SSE early) AND fire the BE-side
    // DELETE so the agent loop tears down the kernel/sandbox call —
    // closing the socket alone doesn't always reach the runner in time.
    abortRef.current?.abort();
    void api.cancelMessage(investigationId);
  }, [investigationId]);

  return { log, send, cancel };
}

export function AgentProvider({
  investigationId,
  children,
}: {
  investigationId: string;
  children: React.ReactNode;
}) {
  const value = useAgentInternal(investigationId);
  return (
    <AgentContext.Provider value={value}>{children}</AgentContext.Provider>
  );
}

export function useAgent(): AgentState {
  const ctx = useContext(AgentContext);
  if (!ctx) {
    throw new Error("useAgent must be used inside <AgentProvider>");
  }
  return ctx;
}
