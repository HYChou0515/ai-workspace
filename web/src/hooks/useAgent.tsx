import { createContext, useContext, useMemo } from "react";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import {
  getStored as getKbEnhancementSelection,
  toBodyEnhancements,
} from "../lib/kbEnhancementMode";
import { getReasoningEffort } from "../lib/reasoningEffort";
import { getKbSearchMax } from "../lib/kbSearchMax";
import { getKbWikiMax } from "../lib/kbWikiMax";
import type { AgentLog } from "../pages/investigation/agentLog";
import {
  type BroadcastChatTransport,
  type ChatConnection,
  STORE_POLL_MS,
  useChatSession,
} from "./useChatSession";
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

export type AgentState = {
  /** The investigation this agent context belongs to — the kernel/file APIs
   * (notebook cell execution) are scoped to it. */
  investigationId: string;
  log: AgentLog;
  /** Whether this viewer is actually receiving live events (#493 / L3). */
  connection: ChatConnection;
  /** Enqueue an interactive turn. `opts.imagePaths` carries the composer's
   * attached image workspace paths so a VLM main model sees them inline (no
   * read_image round-trip); a text-only model ignores them. `opts.applySkills` (#380) loads the named skills
   * into THIS turn (one-shot, chosen from the Skills panel); the composer clears
   * them after sending. */
  send: (
    content: string,
    opts?: { applySkills?: string[]; imagePaths?: string[]; answers?: string },
  ) => Promise<void>;
  /** @mention people to "come look" — notifies them, does NOT run the agent. */
  mention: (userIds: string[], note: string) => Promise<void>;
  cancel: () => void;
  /** Undo the last `turns` whole turns (#38), then re-snapshot the thread. */
  undo: (turns: number) => Promise<void>;
};

const AgentContext = createContext<AgentState | null>(null);

export function useAgentInternal(
  investigationId: string,
  pollMs: number = STORE_POLL_MS,
): AgentState {
  const slug = useWorkspaceSlug();
  // The turn state machine is shared with `useItemChat` (see useChatSession);
  // this hook only says WHICH endpoints an item's DEFAULT chat talks to.
  const transport = useMemo<BroadcastChatTransport>(
    () => ({
      threadKey: investigationId,
      queryKey: qk.conversation(investigationId),
      filesKey: qk.files(investigationId),
      getThread: () => api.getConversation(investigationId),
      subscribe: (signal, since) => api.subscribeInvestigation(slug, investigationId, signal, since),
      post: (content, opts) =>
        api.sendMessage({
          slug,
          investigationId,
          content,
          reasoningEffort: getReasoningEffort() ?? undefined,
          // Knowledge-search depth + the "Search the wiki" toggle → this turn's
          // ask_knowledge_base lookups (the RCA→KB bridge routes chunk/wiki/both).
          enhancements: toBodyEnhancements(getKbEnhancementSelection()),
          // #334: per-message kb_search-count cap, shared across this turn's
          // ask_knowledge_base calls.
          maxKbSearches: getKbSearchMax(),
          // #537 follow-up: the wiki twin (sticky shared with the KB chat).
          maxWikiSearches: getKbWikiMax(),
          // #380: skills the user queued in the Skills panel to apply THIS turn
          // (hard-loaded into the agent's context). Empty/absent → nothing forced.
          applySkills: opts?.applySkills,
          // Attached image workspace paths — a VLM main model reads them inline.
          imagePaths: opts?.imagePaths,
          // grill-me: the `ask_user` question this message answers, when the
          // user clicked an option instead of typing.
          answers: opts?.answers,
        }),
      requestCancel: () => api.cancelMessage(slug, investigationId),
      undoTurns: async (turns) => {
        await api.undoTurns(slug, investigationId, turns);
      },
      addMention: (userIds, note) => api.addMention(slug, investigationId, userIds, note),
    }),
    [slug, investigationId],
  );

  const session = useChatSession(transport, pollMs);
  return { investigationId, ...session };
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
