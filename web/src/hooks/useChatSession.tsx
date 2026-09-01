import { useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import type { AgentEvent } from "../events";
import { eventId, eventSeq, isTerminal, isTurnProgress } from "../events";
import { type AgentLog, logFromMessages, reduceAgent } from "../pages/investigation/agentLog";
import type { MsgKey } from "../lib/i18n";
import { type QuotaDetail, type QuotaKind, quotaMessage } from "../lib/quotaFailure";
import { type ChatThread, useChatLog } from "./useChatLog";
import { useCurrentUser } from "./useCurrentUser";
import { useT } from "../lib/i18n";
import { STORE_POLL_MS, useStorePollFallback } from "./useStorePollFallback";

/** A quota refusal is the one send failure the user can do something about, so
 *  the composer names which limit and where to go rather than echoing a status.
 *  Three rules answer 507 and the remedies are three different places. */
const CHAT_QUOTA_KEY = {
  workspace: "chat.send.workspaceFull",
  user: "chat.send.userFull",
  environment: "chat.send.envFull",
} as const satisfies Record<NonNullable<QuotaKind>, MsgKey>;

export { STORE_POLL_MS };
export type { ChatThread };

/**
 * The chat-turn state machine for a #43 BROADCAST chat, written once.
 *
 * `useAgent` (an item's default chat) and `useItemChat` (a named chat) were
 * line-for-line the same machine over two different clients, and they drifted:
 * the #493 auto-reconnect and gateway tolerance landed in one and never crossed
 * to the other, so a WorkItem chat went permanently deaf on a single dropped
 * stream. Both now supply a {@link BroadcastChatTransport} and share this body,
 * so a fix can no longer reach one surface and miss the other.
 *
 * Broadcast semantics (#43): the POST only ENQUEUES the turn — the user's own
 * message and every turn event come back over the shared subscription, so all
 * viewers see the turn, and nothing is pushed optimistically here (that would
 * double it).
 */

export type ChatSendOpts = {
  applySkills?: string[];
  imagePaths?: string[];
  /** grill-me: the `ask_user` question this message answers, when the user
   * clicked an option instead of typing. */
  answers?: string;
};

export type BroadcastChatTransport = {
  /** Identity of the thread. A change resets the log and re-subscribes. */
  threadKey: string;
  /** react-query key holding the persisted thread. */
  queryKey: QueryKey;
  /** react-query key to invalidate when a `file_changed` event arrives. */
  filesKey: QueryKey;
  /** #613: react-query key the chat's todo checklist lives under — a
   * `todos_updated` event writes the new list straight into it (and a terminal
   * event invalidates it, the backstop for updates missed while disconnected).
   * Optional: transports without a todo surface just omit it. */
  todosKey?: QueryKey;
  /** #613 P3: react-query key the chat's goal lives under — a `goal_updated`
   * event merges the new goal state into it; a terminal goal state (met /
   * exhausted) also refetches the thread so the persisted marker appears. */
  goalKey?: QueryKey;
  /** #739: react-query key the chat's context gauge lives under. A terminal
   * event refetches it, because the usage only changes when a turn reports what
   * the provider actually read — and a gauge that never moves gets believed. */
  contextKey?: QueryKey;
  /** Read the persisted thread. `null` = no thread yet. */
  getThread: () => Promise<ChatThread | null>;
  /** The long-lived broadcast subscription. `since` (passed only on a RECONNECT)
   * asks the server to first replay the events after that broadcast seq — the
   * same-pod gap — before resuming live. */
  subscribe: (signal: AbortSignal, since?: number) => AsyncIterable<AgentEvent>;
  /** Enqueue a turn (202; the events arrive on the subscription). */
  post: (content: string, opts?: ChatSendOpts) => Promise<void>;
  /** Tell the backend to tear down the in-flight turn. Returning the promise
   * lets a FAILED stop be surfaced — the UI has already said it stopped. */
  requestCancel: () => void | Promise<unknown>;
  undoTurns: (turns: number) => Promise<void>;
  addMention: (userIds: string[], note: string) => Promise<void>;
};

/** Whether this viewer is actually receiving live events.
 *
 * Losing the stream used to be completely invisible: the subscription's `catch`
 * swallowed every non-abort error — no banner, no state, not even a
 * `console.error` — so an idle-proxy cut or a pod rollover looked exactly like a
 * chat where nothing was happening, while the answer on screen quietly stopped
 * growing (live events are dropped when nobody is attached; there is no replay).
 *
 * The content is not at risk — the turn is persisted and re-read — so what is
 * left is entirely a matter of TELLING the user. A frozen answer labelled
 * "reconnecting" is a wait; the same frozen answer in silence is a hang they can
 * only read as broken. `attempts` separates a blip from an outage. */
export type ChatConnection = {
  state: "connecting" | "live" | "reconnecting";
  /** Whether a real turn event has actually arrived on this subscription.
   *
   * `subscribe` succeeds on ANY replica: a pod that is not running the turn just
   * creates an empty session for the key and starts heartbeating, so the viewer
   * is subscribed, healthy-looking and completely deaf. "Connected" therefore
   * says nothing about delivery, and asserting it would be a claim we have no
   * evidence for. Presence churn ("someone is typing") does not count — it is
   * not turn progress, and counting it suppressed the cross-pod store-poll in
   * exactly the situation the poll exists for. */
  receiving: boolean;
  /** Why the stream last dropped; null while healthy. */
  error: string | null;
  /** Consecutive failed reconnects (0 while healthy). */
  attempts: number;
};

export type ChatSession = {
  log: AgentLog;
  connection: ChatConnection;
  send: (content: string, opts?: ChatSendOpts) => Promise<void>;
  mention: (userIds: string[], note: string) => Promise<void>;
  cancel: () => void;
  undo: (turns: number) => Promise<void>;
};

/** Gateway/timeout statuses (and a bare network drop, 0) that mean "the request
 * was cut, but the turn may well be running" — never "the turn failed". */
/** How many delivered event ids a viewer remembers, for the duplicate check.
 *
 * Matched to the server's replay ring (`turns.py` `replay_buffer_events`): what
 * this guards against is a re-delivery of something the SERVER still holds, so
 * remembering further back than the server can replay buys nothing — and a
 * chat left open all day would grow without it. */
const SEEN_IDS_MAX = 2000;

const GATEWAY_CUT = new Set([0, 502, 503, 504]);

const isAbort = (err: unknown) => (err as { name?: string } | null)?.name === "AbortError";

/** The transient "you may have missed a piece" notice, shown while a dropped
 * stream reconnects — and only when a turn was actually being written.
 *
 * It lives no longer than the turn it interrupted, and three things retract it:
 * a contiguous replay (the buffer filled the gap), the re-hydrate after the
 * reconnect finding the turn already ended, and the turn's own terminal event.
 *
 * Contiguity alone was too narrow to hang it on. Each pod numbers its own
 * broadcast, so a reconnect that lands elsewhere can resume in a numbering that
 * was never this viewer's — not always (two pods that both kept a subscriber
 * throughout do agree), but often enough that the test fails on a turn nothing
 * was lost from. The terminal event alone is not enough either: a pod with no
 * subscriber buffers nothing, so a turn that ends during the outage never
 * announces itself. Hence the middle one, which asks the store instead of the
 * stream. A claim that outlives what it describes is read as "you lost
 * something" while the whole answer sits above it. */
const GAP_BANNER = "連線中斷,這裡可能少了一段";

export function useChatSession(
  transport: BroadcastChatTransport,
  pollMs: number = STORE_POLL_MS,
): ChatSession {
  const qc = useQueryClient();
  const t = useT();
  const currentUser = useCurrentUser();
  // Epoch ms of the last live event (or send) — gates the #202 store-poll so a
  // healthy same-pod stream is never polled over.
  const lastEventAtRef = useRef(0);
  // #43 reconnect replay: the highest broadcast seq seen on this subscription so
  // a reconnect can resume from it (`?since=`). Reset when the thread changes so a
  // new thread's seqs (which restart at 1) are tracked, not shadowed by the old.
  const maxSeqRef = useRef(0);
  // Whether a gap banner was added on the last drop and is awaiting confirmation:
  // the reconnect's first event decides if the replay filled the hole (remove) or
  // a real gap remains (keep).
  const gapBannerPendingRef = useRef(false);
  // Whether an answer was actually being written when the connection dropped.
  // Set from turn events, cleared by the terminal one — the only evidence that a
  // hole is even possible. Without it the banner was gated on "the last entry is
  // an assistant message", which is true of every chat that has ever been
  // answered, so an idle window that blinked claimed a missing piece.
  const turnInFlightRef = useRef(false);
  // #43: the ids already drawn, so one event delivered twice is drawn once.
  //
  // Delivery is at-least-once and always was — a reconnect resumes `?since=`
  // against whichever pod answers, and each pod numbers its own broadcast, so
  // the same event can arrive again under a number this viewer never saw.
  // Nothing downstream can catch it: the folds append, and `reconcileSnapshot`
  // reads a longer local log as "the store is behind" and keeps it. Recognising
  // the repeat here is what makes that harmless.
  //
  // Bounded, and by the same reasoning as the server's replay ring: what it
  // protects against is a re-delivery of something recent, so remembering
  // everything forever buys nothing and leaks on a long-lived chat.
  const seenIdsRef = useRef<Set<string>>(new Set());
  const [connection, setConnection] = useState<ChatConnection>({
    state: "connecting",
    receiving: false,
    error: null,
    attempts: 0,
  });
  const { log, setLog, snapshot, reconcile } = useChatLog({
    threadKey: transport.threadKey,
    queryKey: transport.queryKey,
    getThread: transport.getThread,
  });

  // A new thread restarts the broadcast seq at 1, so forget the old thread's max
  // or a stale, larger value would make every reconnect ask to resume past the
  // new thread's events (replaying nothing) forever.
  useEffect(() => {
    maxSeqRef.current = 0;
    seenIdsRef.current = new Set();
    // Whether an answer was being written belongs to the thread we were watching.
    // Carried across, it makes the NEXT thread's first drop claim a hole in a
    // conversation that has never run anything.
    turnInFlightRef.current = false;
  }, [transport.threadKey]);

  // The long-lived broadcast subscription (#43) with the #493 auto-reconnect:
  // the stream can drop mid-turn (an idle ingress cut, a pod rollover). A dropped
  // stream used to be swallowed, leaving the viewer stuck forever. Instead, back
  // off, re-hydrate (so a turn that finished during the gap shows up) and
  // re-subscribe. Only an abort (unmount / thread switch) stops the loop.
  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;
    const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
    void (async () => {
      let backoff = 1000;
      // The FIRST subscribe of this effect is a fresh connect — no replay. Every
      // later one is a RECONNECT and resumes from the last seq we saw, so the
      // events emitted during the gap are replayed on the same pod.
      let firstConnect = true;
      while (!stopped) {
        const since = firstConnect ? undefined : maxSeqRef.current;
        firstConnect = false;
        let firstEventThisConnect = true;
        try {
          // Opening the stream clears the error, but NOT the attempt count: being
          // subscribed is not the same as receiving, and a socket that opens and
          // immediately dies would otherwise reset the counter every cycle and
          // make a sustained outage look like an endless first blip.
          setConnection((c) => (c.state === "live" ? c : { ...c, state: "live", error: null }));
          for await (const ev of transport.subscribe(controller.signal, since)) {
            backoff = 1000; // a healthy stream resets the backoff
            // Track the highest broadcast seq so the next reconnect resumes here.
            const seq = eventSeq(ev);
            if (seq !== undefined && seq > maxSeqRef.current) maxSeqRef.current = seq;
            // Already drawn — a replay of something this viewer has. `continue`
            // skips the rest of this iteration, the gap-banner check below
            // INCLUDED (an earlier comment here claimed otherwise); that is why
            // the banner no longer hangs on the seq test alone — the turn's own
            // terminal event retracts it.
            // An event with no id (a backend that predates them) is always
            // folded: dropping those would blank the stream during a rollout.
            const id = eventId(ev);
            if (id !== undefined) {
              if (seenIdsRef.current.has(id)) continue;
              seenIdsRef.current.add(id);
              if (seenIdsRef.current.size > SEEN_IDS_MAX) {
                // Oldest-first: a Set iterates in insertion order.
                const drop = seenIdsRef.current.size - SEEN_IDS_MAX;
                let n = 0;
                for (const old of seenIdsRef.current) {
                  if (n++ >= drop) break;
                  seenIdsRef.current.delete(old);
                }
              }
            }
            // On the first event after a reconnect, decide the fate of the gap
            // banner: a contiguous replay (the very next seq) means the same-pod
            // buffer filled the hole, so drop the banner; a jump means a real gap
            // survived it, so keep it.
            if (firstEventThisConnect) {
              firstEventThisConnect = false;
              if (gapBannerPendingRef.current) {
                const contiguous = seq !== undefined && since !== undefined && seq === since + 1;
                if (contiguous) {
                  setLog((prev) => {
                    const last = prev.entries[prev.entries.length - 1];
                    return last?.kind === "banner" && last.text === GAP_BANNER
                      ? { ...prev, entries: prev.entries.slice(0, -1) }
                      : prev;
                  });
                }
                gapBannerPendingRef.current = false;
              }
            }
            // A real TURN event is the proof the stream works — only now is the
            // outage over. Presence is excluded for the same reason as above: a
            // pod that is not running the turn still broadcasts it.
            if (ev.type !== "presence") {
              setConnection((c) =>
                c.state === "live" && c.attempts === 0 && c.receiving
                  ? c
                  : { state: "live", receiving: true, error: null, attempts: 0 },
              );
            }
            // Only a real turn event proves this viewer is on the turn's pod.
            // Presence churn is broadcast by the session regardless, so counting
            // it kept the #202 store-poll dormant in exactly the cross-pod case
            // the poll exists for.
            if (ev.type !== "presence") lastEventAtRef.current = Date.now();
            // Is an answer being written right now? Only that makes a hole
            // possible, and only a turn-progress event can say so — the log's own
            // shape cannot ("the last entry is an assistant message" is the
            // resting state of every answered chat), and neither can "not
            // presence": `file_changed` fires on any editor save, with no turn
            // anywhere. Terminal clears it in its own branch below; everything
            // else — retry notices included — leaves it untouched.
            if (isTurnProgress(ev)) turnInFlightRef.current = true;
            if (ev.type === "file_changed") {
              // A human edited a workspace file — refetch the tree. Not a turn
              // event, so it never folds into the log.
              void qc.invalidateQueries({ queryKey: transport.filesKey });
              continue;
            }
            if (ev.type === "todos_updated") {
              // #613: the agent rewrote the todo checklist — the event carries
              // the whole new list, so write it straight into the cache (no
              // refetch). Not a transcript event; it never folds into the log.
              if (transport.todosKey) qc.setQueryData(transport.todosKey, ev.items);
              continue;
            }
            if (ev.type === "goal_updated") {
              // #613 P3: merge the new goal state (the event has no
              // checker_enabled — that's deploy config, keep the cached one).
              if (transport.goalKey) {
                qc.setQueryData(
                  transport.goalKey,
                  (old: { checker_enabled?: boolean } | undefined) => ({
                    goal: ev.goal,
                    checker_enabled: old?.checker_enabled ?? true,
                  }),
                );
              }
              // A terminal goal state persists a marker message AFTER the turn's
              // own terminal refetch — pull the thread again so it shows up.
              if (ev.goal === null || ev.goal.state !== "active") {
                void qc.invalidateQueries({ queryKey: transport.queryKey });
              }
              continue;
            }
            setLog((prev) => reduceAgent(prev, ev));
            if (isTerminal(ev)) {
              // The gap banner was a claim about THIS turn. The turn is over and
              // the thread is re-read below, so the answer on screen is the
              // stored one and the claim describes nothing — whatever the seq
              // numbers said. Leaving it is the false alarm users lose
              // confidence over: they are told they missed something while
              // looking at the whole answer.
              gapBannerPendingRef.current = false;
              turnInFlightRef.current = false;
              setLog((prev) =>
                prev.entries.some((e) => e.kind === "banner" && e.text === GAP_BANNER)
                  ? {
                      ...prev,
                      entries: prev.entries.filter(
                        (e) => !(e.kind === "banner" && e.text === GAP_BANNER),
                      ),
                    }
                  : prev,
              );
              // #613: catch up on todo updates missed while disconnected — the
              // stream has no replay across pods, but the turn just ended, so
              // one refetch reconciles the panel cheaply.
              if (transport.todosKey)
                void qc.invalidateQueries({ queryKey: transport.todosKey });
              // #739: the turn just told us what the window really holds.
              if (transport.contextKey)
                void qc.invalidateQueries({ queryKey: transport.contextKey });
              // Re-snapshot from the store — it carries the BE-attached
              // `ask_knowledge_base` citations the stream doesn't emit.
              const fresh = await transport.getThread();
              if (fresh) {
                qc.setQueryData(transport.queryKey, fresh);
                // The terminal event is published BEFORE the turn is persisted,
                // so this read can legitimately lose the race — reconcile, never
                // replace, or the just-streamed reply is wiped with no later
                // event to put it back.
                reconcile(fresh);
              }
            }
          }
        } catch (err: unknown) {
          if (isAbort(err)) return; // unmount / thread switch
          // Anything else → say so, then fall through to the reconnect delay.
          // Swallowing this is what made a dropped stream indistinguishable from
          // a quiet one.
          const why = err instanceof Error ? err.message : String(err);
          setConnection((c) => ({
            state: "reconnecting",
            receiving: false,
            error: why,
            attempts: c.attempts + 1,
          }));
        }
        // A stream that ENDS without throwing (the server closed it) is also a
        // lost connection, not a finished chat.
        setConnection((c) =>
          c.state === "reconnecting"
            ? c
            : { state: "reconnecting", receiving: false, error: null, attempts: c.attempts + 1 },
        );
        if (stopped) return;
        // Events published while nobody is attached are dropped and never
        // replayed, so an answer that resumes after this gap is missing a piece
        // and rejoins mid-sentence. Splicing the two halves together silently
        // presents a mutilated answer as a whole one — say where the hole is.
        // A hole only matters where there was something to interrupt, and the
        // only witness to that is a turn event seen on this connection —
        // `turnInFlightRef`. Not `streaming` (the subscription starts before
        // hydration resolves, so it can be false while text is visibly
        // arriving), and no longer "the last entry is an assistant message":
        // that is the resting state of every answered chat, so an idle window
        // that blinked announced a missing piece and, with no further event
        // coming, never took it back.
        const interruptedATurn = turnInFlightRef.current;
        turnInFlightRef.current = false;
        // What the store held before the outage, so the re-hydrate below can tell
        // "an answer landed while we were away" from "the thread is unchanged".
        //
        // `undefined` is the only real absence of a baseline — hydration had not
        // resolved. An EMPTY thread is a baseline of zero, and every chat's first
        // turn starts there; reading that as "cannot tell" withheld the
        // retraction exactly where a brand-new conversation lives.
        const cachedBefore = qc.getQueryData<ChatThread | null>(transport.queryKey);
        const hadBaseline = cachedBefore !== undefined;
        const persistedBefore = cachedBefore?.messages.length ?? 0;
        setLog((prev) =>
          interruptedATurn
            ? {
                ...prev,
                entries: [
                  ...prev.entries,
                  { kind: "banner", at: Date.now(), text: GAP_BANNER },
                ],
              }
            : prev,
        );
        // Let the reconnect's first event confirm whether the replay filled the
        // gap (remove the banner) or a real hole remains (keep it). Only when
        // one was actually added — a pending flag with no banner behind it would
        // spend the retraction on nothing.
        gapBannerPendingRef.current = interruptedATurn;
        await sleep(backoff);
        if (stopped) return;
        const fresh = await transport.getThread().catch(() => null);
        if (fresh && !stopped) {
          qc.setQueryData(transport.queryKey, fresh);
          // A drop mid-turn re-hydrates a thread that does NOT yet contain the
          // answer being streamed — reconcile so reconnecting never costs the
          // user what they were reading.
          reconcile(fresh);
          // …and if the store says the turn ENDED while we were away, the hole is
          // closed by this very read: the whole answer is on screen, from the
          // store. The terminal event cannot be relied on to say so — a pod with
          // no subscriber buffers nothing, so a turn that finishes during the
          // outage never announces itself to the reconnected stream, and a banner
          // waiting for that announcement waits forever.
          //
          // The evidence has to be an ANSWER THAT ARRIVED: the thread grew while
          // we were away AND now ends on an assistant message. "Ends on something
          // other than a user message" is not the same thing and deletes live
          // banners — a workflow chat never persists a user message at all (its
          // tail is the previous node's reply while the next one streams), and a
          // `notice` (#624) or a human `mention` can be the tail at any moment.
          const last = fresh.messages[fresh.messages.length - 1];
          // With nothing to compare against, "it grew" is true of every non-empty
          // thread and this collapses back into the over-broad rule it replaced.
          // No baseline, no verdict — but an empty thread IS a baseline.
          const grew = hadBaseline && fresh.messages.length > persistedBefore;
          if (grew && last !== undefined && last.role === "assistant") {
            gapBannerPendingRef.current = false;
            setLog((prev) =>
              prev.entries.some((e) => e.kind === "banner" && e.text === GAP_BANNER)
                ? {
                    ...prev,
                    entries: prev.entries.filter(
                      (e) => !(e.kind === "banner" && e.text === GAP_BANNER),
                    ),
                  }
                : prev,
            );
          }
        }
        backoff = Math.min(backoff * 2, 15000);
      }
    })();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [transport, qc, reconcile]);

  // #202 cross-pod safety net: when this viewer's stream is on a pod that isn't
  // running the turn, the broadcast yields nothing and the composer would stay
  // stuck on "working…". While streaming AND the stream is silent, poll the
  // persisted thread (shared store, any pod serves it): surface the user's own
  // just-sent message, and clear "streaming" once the reply is persisted — never
  // regressing a log the live stream may already have advanced.
  useStorePollFallback({
    active: log.streaming,
    isLive: () => Date.now() - lastEventAtRef.current < pollMs,
    fetchThread: () => transport.getThread(),
    onSnapshot: (thread) => {
      if (!thread) return;
      const msgs = thread.messages;
      const last = msgs[msgs.length - 1];
      const done = last !== undefined && last.role !== "user";
      if (done) {
        // The store has told us the turn is over — for a CROSS-POD viewer the
        // only such word that will ever arrive, since no terminal event is
        // coming and the retraction below reads the cache the line beneath
        // advances. So it ends the turn for the gap banner too.
        //
        // But on the STRICT reading, not this `done`. "The tail is not a user
        // message" is true mid-turn twice over: a human `mention` lands at any
        // moment, and the #624 `notice` is persisted before the model is even
        // called, so it stands through the whole time-to-first-token window.
        // Disarming there means a real hole later in that turn raises nothing —
        // silence, which is the failure nobody reports. Only an assistant answer
        // says a turn produced its result.
        if (last.role === "assistant") turnInFlightRef.current = false;
        qc.setQueryData(transport.queryKey, thread);
        reconcile(thread);
        return;
      }
      const snap = logFromMessages(msgs);
      setLog((prev) =>
        snap.entries.length > prev.entries.length ? { ...snap, streaming: true } : prev,
      );
    },
    // The poll IS the safety net for a cross-pod viewer. If it is failing too,
    // there is no live stream AND no fallback — the worst state available, and
    // it used to look identical to "nothing has happened yet".
    onError: (err) => {
      const why = err instanceof Error ? err.message : String(err);
      setConnection((c) => ({
        state: "reconnecting",
        receiving: false,
        error: why,
        attempts: c.attempts + 1,
      }));
    },
    pollMs,
  });

  const send = useCallback(
    async (content: string, opts?: ChatSendOpts) => {
      const trimmed = content.trim();
      if (!trimmed) return;
      // Flip into "streaming" eagerly so the composer locks, but DON'T push the
      // user message — it arrives via the `user_message` broadcast (#43). Stamp
      // activity so the #202 poll gives the live stream one cycle to start.
      lastEventAtRef.current = Date.now();
      setLog((prev) => ({ ...prev, streaming: true, error: null, metrics: null }));
      try {
        await transport.post(trimmed, opts);
      } catch (err: unknown) {
        if (isAbort(err)) return;
        // #493: a gateway cut does NOT mean the turn failed — the POST may have
        // been dropped by an idle proxy while the turn runs server-side. Stay in
        // "streaming" so the stream / store-poll surfaces the result, instead of
        // flashing an error the user has to dismiss while the answer arrives.
        const status = (err as { status?: number } | null)?.status;
        if (status !== undefined && GATEWAY_CUT.has(status)) {
          lastEventAtRef.current = Date.now(); // give the poll a grace cycle
          return;
        }
        // A quota refusal is the one send failure the user can act on, so it
        // names which limit and where to go. Everything else keeps reporting
        // what actually happened rather than guessing.
        // Every limit that bound (not just the one that fired first) plus the
        // numbers behind it — see `quotaMessage`.
        const quota = quotaMessage(t, CHAT_QUOTA_KEY, {
          ...(err as { code?: string; also?: string[]; detail?: QuotaDetail } | null),
          status,
        });
        const msg =
          quota ??
          // #714: the send was refused because the backend could not establish
          // who is asking. "messages failed: 500" is visible and useless; the
          // one thing the person can do about it is sign in again.
          ((err as { code?: string } | null)?.code === "request_env_failed"
            ? t("chat.send.identityUnavailable")
            : err instanceof Error
              ? err.message
              : String(err));
        // Not the turn's error: no turn ran. It says which limit bound and
        // links to it, and it must survive whatever the stream does next.
        setLog((prev) => ({ ...prev, streaming: false, error: msg, errorFromTurn: false }));
      }
    },
    [transport, t],
  );

  const cancel = useCallback(() => {
    // The turn runs server-side over the broadcast — there's no local fetch to
    // abort. Tell the BE to tear it down, and flip out of "streaming" right now
    // (#49): teardown can lag on a long exec, and the user pressed Stop.
    // Flip out of "streaming" right now (#49) — the user pressed Stop and
    // teardown can lag on a long exec. But if the request to stop never lands,
    // saying nothing means the turn runs on invisibly and the UI has already
    // told them it stopped.
    void Promise.resolve(transport.requestCancel()).catch((err: unknown) => {
      const why = err instanceof Error ? err.message : String(err);
      setLog((prev) => ({
        ...prev,
        error: `停止失敗,這一輪可能仍在進行:${why}`,
        errorFromTurn: false,
      }));
    });
    setLog((prev) => ({ ...prev, streaming: false }));
  }, [transport]);

  const undo = useCallback(
    async (turns: number) => {
      // #38: drop the last `turns` whole turns server-side, then re-snapshot.
      // Files aren't reverted — the caller's confirm copy says so.
      if (turns <= 0) return;
      await transport.undoTurns(turns);
      const fresh = await transport.getThread();
      qc.setQueryData(transport.queryKey, fresh ?? null);
      snapshot(fresh);
    },
    [transport, qc, snapshot],
  );

  const mention = useCallback(
    async (userIds: string[], note: string) => {
      if (userIds.length === 0) return;
      await transport.addMention(userIds, note);
      // Optimistic: a mention is its own log entry, not an agent turn.
      setLog((prev) => ({
        ...prev,
        entries: [
          ...prev.entries,
          { kind: "mention", by: currentUser, users: userIds, note, at: Date.now() },
        ],
      }));
    },
    [transport, currentUser],
  );

  return { log, connection, send, mention, cancel, undo };
}
