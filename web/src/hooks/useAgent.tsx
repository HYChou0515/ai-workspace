import { useQuery } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import {
  EMPTY_LOG,
  type AgentLog,
  logFromMessages,
  reduceAgent,
} from "../pages/investigation/agentLog";
import { useCurrentUser } from "./useCurrentUser";

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
  const currentUser = useCurrentUser();
  const [log, setLog] = useState<AgentLog>(EMPTY_LOG);
  const abortRef = useRef<AbortController | null>(null);

  // Hydrate the persisted conversation. staleTime 0 so each mount sees the
  // turns the backend persisted after the last stream; the guard below keeps a
  // refetch from clobbering the log we're streaming into.
  const { data: conv } = useQuery({
    queryKey: qk.conversation(investigationId),
    queryFn: () => api.getConversation(investigationId),
    staleTime: 0,
  });

  // Reset + abort the running stream when switching investigations.
  const hydratedFor = useRef<string | null>(null);
  useEffect(() => {
    hydratedFor.current = null;
    setLog(EMPTY_LOG);
    return () => abortRef.current?.abort();
  }, [investigationId]);

  // Seed the log from the hydrated conversation, once per thread.
  useEffect(() => {
    if (conv === undefined || hydratedFor.current === investigationId) return;
    hydratedFor.current = investigationId;
    setLog(conv ? logFromMessages(conv.messages) : EMPTY_LOG);
  }, [conv, investigationId]);

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
            message: { role: "user", author: currentUser, content: trimmed },
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
    [investigationId, currentUser],
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
