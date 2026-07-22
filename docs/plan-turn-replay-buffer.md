# Plan — Same-pod reconnect lossless: in-pod broadcast replay buffer

## Problem

The `#43` live broadcast (`ChatTurnEngine` / `_WorkspaceSession` in
`src/workspace_app/api/turns.py`) fans each turn event out to the subscriber
queues that are **attached at that instant** and keeps **no buffer**:

```python
def publish(self, event: AgentEvent) -> None:
    for q in self.subscribers:
        q.put_nowait(event)
```

So when an SSE stream drops (idle ingress/proxy cut, network blip) the events
emitted **during the disconnected gap are lost forever** — even if the client
reconnects to the **same pod** still running the turn. Nothing recovers them
mid-turn either: a turn's result is persisted to the store only once, in
`_run_turn`'s `finally` via `on_complete` (turn-end), so the store has nothing
for the in-flight turn to re-hydrate from. The viewer sees the "連線中斷,這裡
可能少了一段" banner and permanently misses the middle of the answer; the final
result still arrives (re-hydrate at turn-end), but the live progress is gone.

This is the **same-pod** case. (The cross-pod case — stream on a pod that is not
running the turn at all — is the separate `#202`/sticky problem and is out of
scope here; the store-poll fallback keeps it usable.)

## Goal

On a **same-pod reconnect**, replay exactly the events the client missed during
the gap, so the live answer is reconstructed losslessly and the "少了一段"
banner is suppressed when the gap was actually recovered.

Non-goals: cross-pod live streaming (needs a shared event bus / sticky — separate
work); changing what is persisted to the store; late-joiner replay on first load.

## No specstar migrate

Pure in-pod, in-memory. **No new specstar model, no new indexed field, no
persisted-schema change.** The seq counter and the ring buffer live on
`_WorkspaceSession` and are GC'd with the session (`forget`). `seq` is injected
into the SSE JSON at serialization time (transport concern), never stored. A pod
restart drops the session and resets seq to 0 — the client's `since` then exceeds
the fresh buffer, so it degrades to the existing re-hydrate + banner path. No
persistence is needed for correctness.

## Resume protocol (locked: **seq + `?since=`**)

- Each broadcast event carries a **per-session monotonic `seq`** (never reset),
  injected into the SSE `data:` JSON payload (`to_sse(event, seq)`), **not** into
  the frozen event dataclasses.
- The FE tracks the max `seq` it has seen. On **reconnect** it opens
  `/…/stream?since=<maxSeq>`; the server replays buffered events with `seq > since`
  in order, then continues live.
- Chosen over native SSE `id:` + `Last-Event-ID` (would force the FE off its
  fetch-based reader onto `EventSource`) and over "always replay whole buffer +
  FE rebuild" (pushes reset/dedup complexity into the FE). seq + `?since=` is
  incremental: the FE never resets or dedups — it only ever receives events it has
  not seen.

## Buffer (locked)

- **seq**: `_WorkspaceSession._seq: int`, per-session, monotonic, **never reset**
  (so `?since=` is globally unambiguous). GC'd with the session.
- **ring**: `deque[tuple[int, AgentEvent]]` with **`maxlen`** — count-based,
  default **2000**, configurable (`turn_replay_buffer_events`; `0` disables
  replay). Chosen over byte-bounded: tool outputs are already ceiling-capped and a
  turn does not emit thousands of large events, so `maxlen=2000` bounds memory to
  the same ballpark (~200KB/session) with a one-line `deque(maxlen=N)` instead of a
  running-total + evict loop. 2000 comfortably covers a whole long turn's
  per-chunk `MessageDelta`s, so same-pod reconnect is lossless in practice.
- **Contents**: every published event **except `Presence`** gets a seq and enters
  the ring. `Presence` is an ephemeral roster snapshot — replaying a stale roster
  is pointless, and `subscribe_sse` already re-broadcasts a fresh `Presence` on
  join. (Consistent with the FE already excluding presence from its liveness ref,
  `useChatSession.tsx:158`.)
- **Overflow** (gap older than the ring): the client's `since` is older than the
  oldest retained seq → the missed head was evicted → **fall back to the existing
  re-hydrate + "少了一段" banner**. No regression; strict improvement inside the
  window.

## Atomicity (implementation note)

asyncio is single-threaded. `publish` (seq++ → append to ring → put on every
subscriber queue) is one synchronous, non-`await` block. `subscribe_sse` attaches
its queue **and** snapshots the replay list (`[(s,e) for s,e in ring if s > since]`)
in one synchronous block too. Because neither yields, the replay-list / live-queue
boundary is a single seq — **no duplicates, no gaps**. `_frames` yields the replay
list first, then drains the live queue; both serialize via `to_sse(event, seq)`.

## Scope of surfaces

The change is in `_WorkspaceSession` / `ChatTurnEngine`, so it uniformly benefits
every surface on the `#43` broadcast: app default chat (`useItemChat` via
`AgentPanel`), `useAgent`, named chats, and workflow-run streams.

**KB chat is exempt** — `useKbChat` streams the turn back on the requester's own
`POST …/messages` connection (per-requester `stream()`), with no independent
broadcast `/stream`, so there is no cross-connection gap to replay.

## Definition of Done (verify against these)

- [ ] Same-pod SSE drop mid-turn + reconnect replays exactly the missed events,
      in order, with **no duplication** and no double-applied deltas.
- [ ] The "連線中斷,這裡可能少了一段" banner is **suppressed** when the replay was
      contiguous (`first event's seq == since + 1`), and still **shown** when the
      gap exceeded the buffer (`first seq > since + 1`).
- [ ] First-time connect (no prior stream) is **unchanged** — no `since`, no
      replay; existing re-hydrate behavior intact.
- [ ] `Presence` is never buffered and never carries a seq.
- [ ] `seq` is monotonic per session across multiple turns; GC'd on `forget`.
- [ ] `turn_replay_buffer_events` config knob works; `0` disables replay (falls
      back to today's behavior) and is covered.
- [ ] KB chat is untouched and still works.
- [ ] Whole-project `ty` clean, `ruff check` + `ruff format --check` clean.
- [ ] 100% coverage gate green (full local suite); CI unit suite green.

## TDD phases (flat integer; commit per phase)

- **P1 — backend seq + ring + replay** (`turns.py`, `events.py`):
  `_WorkspaceSession` seq counter + `deque(maxlen=N)`; `publish` stamps seq
  (except `Presence`) and buffers; `subscribe_sse(..., since)` atomically replays
  `seq > since` then goes live; `to_sse(event, seq)` injects `seq`. Tests: missed
  events replayed in order / no dup; `Presence` excluded; seq monotonic across
  turns; `since` older than evicted → replay starts above `since + 1` (gap);
  `since=None` → no replay (today's behavior).
- **P2 — route `?since=`** (`chat_routes.py`, and any other stream endpoints on
  the broadcast): thread the `since` query param into `subscribe_sse`. Tests: the
  param reaches the engine; absent → no replay.
- **P3 — FE resume + banner** (`events.ts`, `real.ts`/`mock.ts`, `sse.ts`,
  `useChatSession.tsx`): `AgentEvent` gains optional `seq`; hook tracks `maxSeq`
  (excluding presence); reconnect passes `?since=maxSeq` (first connect does not);
  banner suppressed on contiguous replay, shown on a real gap; replayed deltas
  reduce without duplication. vitest.
- **P4 — config + polish**: `turn_replay_buffer_events` in the config schema +
  `__main__` + `config.example.yaml` + `app.py` wiring (both engines); `0`
  disables. Whole-project `ty`, `ruff`, and the 100% coverage gate.

## Key files

`src/workspace_app/api/turns.py` (`_WorkspaceSession`, `publish`,
`subscribe_sse`), `src/workspace_app/api/events.py` (`to_sse`),
`src/workspace_app/api/chat_routes.py` (stream endpoints),
`src/workspace_app/api/app.py` + config schema + `config.example.yaml`,
`web/src/events.ts`, `web/src/api/real.ts` + `mock.ts`, `web/src/api/sse.ts`,
`web/src/hooks/useChatSession.tsx`.
