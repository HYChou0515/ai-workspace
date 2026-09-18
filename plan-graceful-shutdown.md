# A pod that goes away must not take the user's chat with it

## The report (2026-09-18)

After #813 the API pods stop OOMing. Three things showed up in their place:

1. **kubelet kills the pod on liveness.** The probe was changed to a `/livez`
   that returns `200 ok` and does nothing — and still fails, so the event loop
   is held for > 30 s. The monster was found: `read_image_impl` calls the VLM
   **synchronously on the event loop** (`agent/tools.py:387`).
2. **Any pod death — HPA scale-down, rollout, liveness kill, OOM — strands the
   chat.** The user's question was accepted (202) and persisted; the turn
   answering it lives only in that pod's memory. Nobody else runs it. The FE
   sees "the last message is the user's, so the reply hasn't arrived" (#559)
   and waits.
3. Target state, in the user's words: on **SIGTERM** (HPA / rollout) the user
   should feel nothing — the reply arrives; on **OOM** (no chance to be
   graceful) the user must at least be able to notice, not wait forever.

## What is true today (verified, not read)

- **SIGTERM never reaches our shutdown code when a chat tab is open.** uvicorn
  0.47 (`server.py:271-301`): on SIGTERM it stops listening, calls
  `connection.shutdown()` on every connection — which for a connection in the
  middle of a response only sets `keep_alive = False` and waits for the response
  to end (`h11_impl.py:340-349`) — then `await asyncio.wait_for(
  self._wait_tasks_to_complete(), timeout=self.config.timeout_graceful_shutdown)`
  with the default `None`, i.e. **waits forever**, and only THEN runs the
  lifespan shutdown. Our SSE streams (`/stream`, `/kb/chats/{id}/stream`,
  `/monitor/stream`) heartbeat until the tab closes. Probed on the real app
  (`sigterm_probe.sh`: boot, hold one `/api/monitor/stream`, `kill -TERM`):
  `Shutting down` → `Waiting for connections to close.` → still running 20 s
  later; `lifespan: shutdown complete` never logged. In k8s that is SIGKILL at
  `terminationGracePeriodSeconds` (unset on `rca-app` ⇒ 30 s). So the turn
  drain written in #558 (`ChatTurnEngine.aclose`, 10 s + 2 s) and
  `registry.close_all()` have **never run in production**.
- **The liveness monster has a sibling.** `describer.answer` / `.describe`
  (`kb/vlm/describer.py`, synchronous streaming) are called from an `async def`
  in two places reachable from the API loop: `read_image_impl`
  (`agent/tools.py:387,389`) and `plot_review.run_review → detect_issues`
  (`agent/plot_review.py:84`, called at ~172). Both relay chunks through
  `on_chunk → ctx.on_exec_output`, which the runner builds as
  `lambda b: queue.put_nowait(...)` on an `asyncio.Queue`
  (`api/litellm_runner.py:1663`) — **not safe to call from a worker thread**,
  so "just `to_thread` it" would trade a stall for a race.
- **Half of "notice" already exists.** `_TurnActivity` (`api/turn_activity.py`)
  is a per-turn heartbeat row (30 s stale) written by the driving pod;
  `GET …/turn/alive` answers it; `AgentPanel` asks after a suspicious silence
  and `TurnStatus` offers a retry when the answer is "nobody". Whether that
  path works in the user's deploy is unverified (P3 probes it with two pods).
- **The cross-pod pieces exist.** Live events fan out to every replica through
  `IEventBus` (RabbitMQ, #611/#614), so a turn running on pod B streams to a
  viewer on pod A. Cross-pod cancel is `TurnEpoch` (#349): any pod advances
  the epoch and the running pod's `_watch_epoch` cancels its turn. The user's
  message is persisted at acceptance (`chat_send.py`, the 202 rule), so the
  question itself is never lost — only the in-flight answer.

## Phases

- **P1 — the VLM leaves the loop.** The runner's `on_exec_output` sink becomes
  thread-safe at the source (`loop.call_soon_threadsafe(queue.put_nowait, …)`
  — the guard belongs where the value is made, so any tool may report from a
  thread). `read_image_impl` and `plot_review.detect_issues` run the describer
  under `asyncio.to_thread`. Test: a describer that sleeps 0.5 s and emits
  three chunks; a loop-lag witness (the shape of `perf_trace._watch_loop_lag`)
  shows the loop stayed free while the tool ran, and the chunks arrived in
  order on the queue. Sweep the class: every synchronous LLM/HTTP call inside an
  `async def` reachable from the API loop (grep `describer.`, `litellm.`,
  `.complete(`, `requests.` in `agent/`, `api/`, `kb/` callers of those).
- **P2 — SIGTERM runs our shutdown, in this order, inside the grace period.**
  `__main__` builds a `uvicorn.Server` subclass whose `handle_exit` (the
  documented seam, `server.py:341`) also schedules `Drain.begin()` on the loop
  via `call_soon_threadsafe`. `Drain.begin()`: (a) flips `/api/readyz` to 503
  (k8s stops routing to this pod; a `preStop` sleep gives the endpoints time
  to update); (b) publishes one terminal `ServerRestart` event to every SSE
  subscriber and closes the streams (#558's `_CLOSE_STREAM` /
  `close_subscribers`) — the FE treats it as "reconnect now", which it already
  does for a dropped connection (#560), so it lands on a live pod; (c) uvicorn's
  wait then ends within a second because the connections are gone, and
  `timeout_graceful_shutdown` (new setting, default 10 s) is the safety net so
  no stuck connection can ever hold the lifespan shutdown again. The lifespan
  then drains turns (`engine.aclose`) and tears down as written. The budgets
  are ONE number: `server.shutdown_budget_sec` (default 20) — the SSE close
  wait, the turn drain and the teardown share it, and the k8s
  `terminationGracePeriodSeconds` must exceed it; the Deployment comment says
  by how much. **k8s side (self-maintained, the PR names it):**
  `terminationGracePeriodSeconds: 60` and `preStop: exec sleep 5` on `rca-app`.
- **P3 — a turn survives its pod.** The turn's durable claim: `_TurnActivity`
  gains `owner` (pod id) and `message_id` (the accepted message being
  answered); the driving pod writes it when the turn starts and heartbeats as
  today. A reclaim sweeper on every API pod (a lease-taking producer in the
  #804 sense — it lists only rows whose heartbeat is stale, a table the size
  of "turns in flight"): CAS-claims the row with its own pod id, **advances the
  `TurnEpoch`** (so a pod that was merely stalled, not dead, cancels its copy —
  no double run), and re-enqueues the persisted message through
  `ChatSendService.send(..., driven_by=RECLAIM)` on its own engine; the new
  turn's events reach the viewer through the event bus. **SIGTERM path:** the
  draining pod gives its turns the drain budget to finish; whatever is still
  running is cancelled (partial persisted, as Stop does) and its claim
  **released** (heartbeat zeroed) so a peer picks it up on its next tick — no
  30 s wait, no user action. **OOM path:** nothing is released; the heartbeat
  goes stale after 30 s; a peer reclaims. The FE shows the #559 waiting state
  during the gap and the #560 reconnect notice; `TurnStatus`'s retry stays as
  the fallback for a fleet with no peer. Re-running means **regenerating from
  the stored question** — a stream cannot be resumed mid-sentence; the dead
  pod's partial fragment is dropped (or kept as an "interrupted" message if the
  thread already persisted one — decide by what Stop persists today).
  Non-goals: resuming a half-answer; workflow runs (they have #429's orphan
  pickup already); the KB chat gets the same treatment only if it shares the
  engine path (it does — `ChatTurnEngine` is one class; verify the claim row is
  keyed the same way).

- **P3 as built (deviations from the paragraph above, decided while reading
  the code).** (1) The claim is its own row, one per turn — `_TurnClaim`,
  keyed by engine key + the user message's `created_at` — not fields on
  `_TurnActivity`: that heartbeat row's explicit "turn ended" write went
  through three timing defects (its module docstring) and was removed; a
  per-turn row is hard-deleted when its turn persists and swept by the
  reclaimer when it lingers, and the heartbeat stays where it is. (2) The
  claim carries the whole send recipe — investigation, conversation, author,
  lane, the `_MessageBody` as JSON — because the persisted `Message` keeps only
  content/author/answers; `apply_skills`, attached images and the retrieval
  knobs would otherwise be lost and the re-run would answer a different
  question. (3) Reclaim rule: `released` (a SIGTERM handover) ⇒ now; else the
  key's heartbeat is stale AND the thread still ends with that user message ⇒
  now; stale but answered ⇒ delete the claim. The taker CAS-claims `owner`,
  advances the epoch, and re-runs through `ChatSendService.rerun` — `_send`
  minus the append and minus the live `UserMessage` publish. (4) A handover
  does NOT persist the partial reply and the cancel marker (that is Stop's
  meaning); the peer's answer follows the question cleanly, and a
  single-replica deploy's restarted pod takes back its own released claims.
  So the phases are: **P3** claims + rerun + reclaim sweeper (the OOM path,
  via staleness); **P4** SIGTERM handover (release + cancel without persist,
  inside the drain budget); **P5** k8s / docs / ledger.

## 判準 (each one a probe through the real door)

- P1: with a 0.5 s blocking describer, the loop-lag witness records < 50 ms lag
  during `read_image`; before the fix it records ≥ 500 ms (the test reddens on
  the unfixed code).
- P2: real app, one SSE held open, `kill -TERM`: the SSE client receives the
  `ServerRestart` event and EOF (not a reset); `lifespan: shutdown complete`
  is logged within `shutdown_budget_sec`; the process exits 0. A turn started
  2 s before SIGTERM finishes and its reply is in the store. `/api/readyz`
  answers 503 from the first millisecond of the drain.
- P3: two real processes on one shared backend + the in-memory event bus
  replaced by the RabbitMQ one where available (else a two-engine in-process
  harness): pod A accepts a message and starts a slow turn; pod A is
  `kill -9`'d; within `TURN_STALE_AFTER_MS + sweep interval` pod B has claimed
  the row, run the turn, and the thread ends with the answer. Same with
  `kill -TERM`: pod B's turn starts within one sweep tick, not 30 s. A stalled
  (not dead) pod A: after B reclaims, A's copy is cancelled by the epoch and
  the thread holds ONE answer.

## Deploy notes (to be carried into the PR body)

- `rca-app`: `terminationGracePeriodSeconds`, `preStop` — both new.
- `server.shutdown_budget_sec` and uvicorn's `timeout_graceful_shutdown` —
  new knobs, ledger row in `docs/migrations.md` §5.5.
- The reclaim sweeper is a pure producer per the #804 convention; the work it
  re-enqueues is a turn, which only an API pod can run — so it stays on the API
  and lists a table bounded by in-flight turns.
