import { useQuery, useQueryClient } from "@tanstack/react-query";
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
import { useCurrentUser } from "./useCurrentUser";
import { useWorkspaceSlug } from "./useWorkspaceSlug";

/**
 * Single source of truth for the agent conversation per investigation.
 *
 * Hydrates Conversation on mount, then (#43) opens a persistent broadcast
 * subscription that drives the log — every viewer sees ALL turns live (whoever
 * sent them). `send` POSTs to enqueue a turn (it no longer streams). The
 * Provider wraps the workspace so both AgentPanel and the bottom panel (which
 * surfaces agent-log lines) read the same log.
 */

type AgentState = {
  /** The investigation this agent context belongs to — the kernel/file APIs
   * (notebook cell execution) are scoped to it. */
  investigationId: string;
  log: AgentLog;
  send: (content: string) => Promise<void>;
  /** @mention people to "come look" — notifies them, does NOT run the agent. */
  mention: (userIds: string[], note: string) => Promise<void>;
  cancel: () => void;
  /** Undo the last `turns` whole turns (#38), then re-snapshot the thread. */
  undo: (turns: number) => Promise<void>;
};

const AgentContext = createContext<AgentState | null>(null);

export function useAgentInternal(investigationId: string): AgentState {
  const slug = useWorkspaceSlug();
  const currentUser = useCurrentUser();
  const qc = useQueryClient();
  const [log, setLog] = useState<AgentLog>(EMPTY_LOG);

  // Hydrate the persisted conversation. staleTime 0 so each mount sees the
  // turns the backend persisted after the last stream; the guard below keeps a
  // refetch from clobbering the log we're streaming into.
  const { data: conv } = useQuery({
    queryKey: qk.conversation(investigationId),
    queryFn: () => api.getConversation(investigationId),
    staleTime: 0,
  });

  // Reset the log when switching investigations. The subscription effect below
  // tears its own controller down via cleanup, keyed on the same id.
  const hydratedFor = useRef<string | null>(null);
  useEffect(() => {
    hydratedFor.current = null;
    setLog(EMPTY_LOG);
  }, [investigationId]);

  // Seed the log from the hydrated conversation, once per thread.
  useEffect(() => {
    if (conv === undefined || hydratedFor.current === investigationId) return;
    hydratedFor.current = investigationId;
    setLog(conv ? logFromMessages(conv.messages) : EMPTY_LOG);
  }, [conv, investigationId]);

  // #43: open the persistent broadcast subscription. Every viewer subscribes
  // here and the stream drives the log — turns posted by anyone show up live.
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        for await (const ev of api.subscribeInvestigation(slug, investigationId, controller.signal)) {
          if (ev.type === "file_changed") {
            // A human edited a workspace file — refetch the file tree. Don't
            // fold it into the agent log (it's a side effect, not a turn event).
            qc.invalidateQueries({ queryKey: qk.files(investigationId) });
            continue;
          }
          setLog((prev) => reduceAgent(prev, ev));
          if (isTerminal(ev)) {
            // Re-snapshot from the persisted conversation — it carries the BE-
            // attached `ask_knowledge_base` citations the SSE stream doesn't
            // emit. Mirrors the old done-handler.
            const fresh = await api.getConversation(investigationId);
            if (fresh) {
              qc.setQueryData(qk.conversation(investigationId), fresh);
              setLog(logFromMessages(fresh.messages));
            }
          }
        }
      } catch (err: unknown) {
        // The subscription is torn down on unmount/investigation-switch via
        // controller.abort() — swallow the resulting AbortError.
        if ((err as { name?: string } | null)?.name === "AbortError") return;
      }
    })();
    return () => controller.abort();
  }, [investigationId, qc]);

  const send = useCallback(
    async (content: string) => {
      const trimmed = content.trim();
      if (!trimmed) return;

      // #43: flip into "streaming" eagerly so the composer locks, but DON'T
      // push the user message — it now arrives via the `user_message`
      // broadcast (pushing here would duplicate it). The turn's events drive
      // the log through the persistent subscription.
      setLog((prev) => ({ ...prev, streaming: true, error: null, metrics: null }));

      try {
        await api.sendMessage({
          slug,
          investigationId,
          content: trimmed,
          reasoningEffort: getReasoningEffort() ?? undefined,
          // Knowledge-search depth + the "Search the wiki" toggle → this turn's
          // ask_knowledge_base lookups (the RCA→KB bridge routes chunk/wiki/both).
          enhancements: withWikiFlag(toBodyEnhancements(getKbEnhancementSelection()), getKbWiki()),
        });
      } catch (err: unknown) {
        if ((err as { name?: string } | null)?.name === "AbortError") return;
        const msg = err instanceof Error ? err.message : String(err);
        setLog((prev) => ({ ...prev, streaming: false, error: msg }));
      }
    },
    [slug, investigationId],
  );

  const cancel = useCallback(() => {
    // #43: the turn runs server-side and streams over the shared broadcast —
    // there's no local send fetch to abort. Fire the BE-side DELETE so the
    // agent loop tears down the in-flight turn (kernel/sandbox).
    void api.cancelMessage(slug, investigationId);
    // #49: flip the UI out of "streaming" RIGHT NOW. The send() promise's
    // `finally` also clears it, but that only fires once the stream tears
    // down — which can lag (a turn stuck in a long exec) or never settle,
    // leaving the Stop button stuck and the composer blocked. The user
    // pressed Stop, so reflect it immediately.
    setLog((prev) => ({ ...prev, streaming: false }));
  }, [slug, investigationId]);

  const undo = useCallback(
    async (turns: number) => {
      // #38: drop the last `turns` whole turns server-side, then re-snapshot
      // the thread (same hydration the post-send tail uses). Files aren't
      // reverted — the caller's confirm copy says so.
      if (turns <= 0) return;
      await api.undoTurns(slug, investigationId, turns);
      const fresh = await api.getConversation(investigationId);
      qc.setQueryData(qk.conversation(investigationId), fresh ?? null);
      setLog(fresh ? logFromMessages(fresh.messages) : EMPTY_LOG);
    },
    [slug, investigationId, qc],
  );

  const mention = useCallback(
    async (userIds: string[], note: string) => {
      if (userIds.length === 0) return;
      await api.addMention(slug, investigationId, userIds, note);
      // Optimistic: a mention is its own log entry (not an agent turn).
      setLog((prev) => ({
        ...prev,
        entries: [
          ...prev.entries,
          { kind: "mention", by: currentUser, users: userIds, note, at: Date.now() },
        ],
      }));
    },
    [slug, investigationId, currentUser],
  );

  return { investigationId, log, send, mention, cancel, undo };
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

/** The agent context if present, else null — for surfaces that may render
 * outside an investigation (e.g. a notebook opened in a KB collection, where
 * there's no kernel to run cells). */
export function useOptionalAgent(): AgentState | null {
  return useContext(AgentContext);
}
