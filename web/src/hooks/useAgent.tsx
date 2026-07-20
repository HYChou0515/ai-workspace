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
  getStored as getKbEnhancementSelection,
  toBodyEnhancements,
} from "../lib/kbEnhancementMode";
import { getReasoningEffort } from "../lib/reasoningEffort";
import { getKbSearchMax } from "../lib/kbSearchMax";
import {
  EMPTY_LOG,
  type AgentLog,
  logFromMessages,
  reduceAgent,
} from "../pages/investigation/agentLog";
import { useCurrentUser } from "./useCurrentUser";
import { STORE_POLL_MS, useStorePollFallback } from "./useStorePollFallback";
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
  /** Enqueue an interactive turn. `opts.imagePaths` carries the composer's
   * attached image workspace paths so a VLM main model sees them inline (no
   * read_image round-trip); a text-only model ignores them. `opts.applySkills` (#380) loads the named skills
   * into THIS turn (one-shot, chosen from the Skills panel); the composer clears
   * them after sending. */
  send: (content: string, opts?: { applySkills?: string[]; imagePaths?: string[] }) => Promise<void>;
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
  const currentUser = useCurrentUser();
  const qc = useQueryClient();
  const [log, setLog] = useState<AgentLog>(EMPTY_LOG);
  // Epoch ms of the last live SSE event (or send) — drives the #202 store-poll
  // gate so a healthy same-pod stream is never polled over.
  const lastEventAtRef = useRef(0);

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
  //
  // #493 symptom 1 (504): the SSE stream can drop mid-turn — an idle ingress cut
  // (now rarer thanks to the server heartbeat), or a pod rollover. A dropped
  // stream USED to be swallowed and left the viewer stuck forever. Instead,
  // AUTO-RECONNECT with capped backoff: on any non-abort end/error, wait, then
  // re-hydrate the persisted thread (so turns that completed during the gap show
  // up) and re-subscribe. Only controller.abort() (unmount / investigation
  // switch) stops the loop.
  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;
    const sleep = (ms: number) =>
      new Promise<void>((r) => setTimeout(r, ms));
    (async () => {
      let backoff = 1000;
      while (!stopped) {
        try {
          for await (const ev of api.subscribeInvestigation(slug, investigationId, controller.signal)) {
            backoff = 1000; // a healthy stream resets the backoff
            // A live event means this viewer IS on the turn's pod — record it so
            // the #202 store-poll fallback stays dormant while the stream flows.
            lastEventAtRef.current = Date.now();
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
          // Torn down on unmount / investigation-switch via controller.abort() —
          // stop the reconnect loop on the resulting AbortError.
          if ((err as { name?: string } | null)?.name === "AbortError") return;
          // Any other error → fall through to the reconnect delay below.
        }
        if (stopped) return;
        // The stream ended (server closed it) or errored: back off, then
        // re-hydrate so a turn that finished during the gap isn't lost, and loop
        // to re-subscribe.
        await sleep(backoff);
        if (stopped) return;
        const fresh = await api.getConversation(investigationId).catch(() => null);
        if (fresh && !stopped) {
          qc.setQueryData(qk.conversation(investigationId), fresh);
          setLog(logFromMessages(fresh.messages));
        }
        backoff = Math.min(backoff * 2, 15000);
      }
    })();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [investigationId, qc]);

  // #202 cross-pod safety net: when this viewer's broadcast is on a pod that
  // isn't running the turn, the stream yields nothing and the composer stays
  // stuck on "working…". While streaming AND the stream is silent, poll the
  // persisted conversation (shared store): surface the user's own just-sent
  // message, and clear "streaming" once the reply is persisted — never
  // regressing a log the live stream may already have advanced.
  useStorePollFallback({
    active: log.streaming,
    isLive: () => Date.now() - lastEventAtRef.current < pollMs,
    fetchThread: () => api.getConversation(investigationId),
    onSnapshot: (conv) => {
      if (!conv) return;
      const msgs = conv.messages;
      const last = msgs[msgs.length - 1];
      const done = last !== undefined && last.role !== "user";
      if (done) {
        qc.setQueryData(qk.conversation(investigationId), conv);
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
    async (content: string, opts?: { applySkills?: string[]; imagePaths?: string[] }) => {
      const trimmed = content.trim();
      if (!trimmed) return;

      // #43: flip into "streaming" eagerly so the composer locks, but DON'T
      // push the user message — it now arrives via the `user_message`
      // broadcast (pushing here would duplicate it). The turn's events drive
      // the log through the persistent subscription.
      // Stamp activity so the #202 poll gives the live stream one cycle to
      // start before it starts polling the store.
      lastEventAtRef.current = Date.now();
      setLog((prev) => ({ ...prev, streaming: true, error: null, metrics: null }));

      try {
        await api.sendMessage({
          slug,
          investigationId,
          content: trimmed,
          reasoningEffort: getReasoningEffort() ?? undefined,
          // Knowledge-search depth + the "Search the wiki" toggle → this turn's
          // ask_knowledge_base lookups (the RCA→KB bridge routes chunk/wiki/both).
          enhancements: toBodyEnhancements(getKbEnhancementSelection()),
          // #334: per-message kb_search-count cap, shared across this turn's
          // ask_knowledge_base calls.
          maxKbSearches: getKbSearchMax(),
          // #380: skills the user queued in the Skills panel to apply THIS turn
          // (hard-loaded into the agent's context). Empty/absent → nothing forced.
          applySkills: opts?.applySkills,
          // Attached image workspace paths — a VLM main model reads them inline.
          imagePaths: opts?.imagePaths,
        });
      } catch (err: unknown) {
        if ((err as { name?: string } | null)?.name === "AbortError") return;
        // #493 symptom 1 (504): a gateway/timeout error (502/503/504) or a bare
        // network drop (status 0) does NOT mean the turn failed — the POST may
        // have been cut by an idle proxy while the turn runs on server-side. Stay
        // in "streaming" so the live SSE stream + #202 store-poll fallback surface
        // the result, instead of falsely flipping to an error the user must clear.
        const status = (err as { status?: number } | null)?.status;
        if (status === 0 || status === 502 || status === 503 || status === 504) {
          lastEventAtRef.current = Date.now(); // give the poll a grace cycle
          return;
        }
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
