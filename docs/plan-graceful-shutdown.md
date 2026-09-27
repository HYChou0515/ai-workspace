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
  `terminationGracePeriodSeconds` (unset on `rca-app` ⇒ 30 s). So whenever a
  chat or monitor stream was open at the signal — a rollout with anyone online
  — the turn drain written in #558 (`ChatTurnEngine.aclose`, 10 s + 2 s) and
  `registry.close_all()` did not run and the pod was SIGKILLed at the grace
  period. (Production's logs were not seen; this is the local probe plus
  uvicorn's code, and it holds exactly when a stream is open.)
- **The liveness monster has a sibling.** `describer.answer` / `.describe`
  (`kb/vlm/describer.py`, synchronous streaming) are called from an `async def`
  in two places found on the first read — `read_image_impl`
  (`agent/tools.py:387,389`) and `plot_review.run_review → detect_issues`
  (`agent/plot_review.py:84`, called at ~172) — plus `read_page_impl` (the P1
  sweep) and `answer_doc_question → land_term_answer → formatter.format`, an
  LLM call when `card_drafter_llm` is wired (round 1). All relay chunks through
  `on_chunk → ctx.on_exec_output`, which the runner builds as
  `lambda b: queue.put_nowait(...)` on an `asyncio.Queue`
  (`api/litellm_runner.py:1663`) — **not safe to call from a worker thread**,
  so "just `to_thread` it" would trade a stall for a race.
- **Half of "notice" already exists.** `_TurnActivity` (`api/turn_activity.py`)
  is a per-KEY heartbeat row (30 s stale) written by the driving pod;
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
- **P2 as built.** No `ServerRestart` event: the drain ENDS each stream (the
  `_CLOSE_STREAM` sentinel → HTTP 200 + EOF), which the FE already treats as
  "reconnect now" (#560, 1 s backoff) — an event the FE would first have to
  learn buys nothing over the EOF it already handles. The one number is
  `server.shutdown_budget_sec` (default 20), passed to uvicorn as
  `timeout_graceful_shutdown` (whole seconds); there is no 10 s setting. The
  process exits 143 (uvicorn re-raises the captured SIGTERM after a clean
  shutdown), not 0. The readiness 503 is the truthful answer, not what stops
  traffic: on a pod DELETION k8s removes the Terminating pod from the
  EndpointSlice on its own, in parallel with `preStop` → SIGTERM, and
  `preStop`'s sleep is what lets that removal propagate before the listener
  closes; on any SIGTERM uvicorn closes the listener within 0.1 s, after
  which a readiness probe is refused outright — the 503 adds nothing to that
  (round 2 measured it; the first correction still credited it with the
  non-deletion case).
- **P3 — a turn survives its pod (the app chat's; see the non-goal).** The turn's durable claim: `_TurnActivity`
  gains `owner` (pod id) and `message_id` (the accepted message being
  answered); the driving pod writes it when the turn starts and heartbeats as
  today. A reclaim sweeper on every API pod (lease-taking like a #804 producer
  but running the turn itself — the third stated exception; it lists every
  open claim and judges afterwards, a table the size of "turns in flight"): CAS-claims the row with its own pod id, **advances the
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
  engine path — **it does not** (round 1): the claim lives in
  `ChatSendService`, and the KB send (`kb_chat_routes.send_message`) builds
  its turn inline and enqueues on `kb_turn_engine` directly, so a KB turn
  opens no claim. KB chat keeps the pre-#815 behaviour: a turn that finishes
  inside the drain budget persists; past it, its cancel persists the partial
  and the "interrupted" marker (Stop's path); on OOM the thread ends on the
  question and `TurnStatus`'s retry is the fallback. Covering it means
  extracting that send into a service with a `rerun` — a follow-up, not this
  PR.

- **P3 as built (deviations from the paragraph above, decided while reading
  the code).** (1) The claim is its own row, one per turn — `TurnClaim`,
  id = engine key + the user message's `created_at` + a unique suffix
  (round 1: two sends in one ms are two rows) — not fields on
  `_TurnActivity`: that heartbeat row's explicit "turn ended" write went
  through three timing defects (its module docstring) and was removed; a
  per-turn row is hard-deleted when its turn persists and swept by the
  reclaimer when it lingers, and the heartbeat stays where it is. (2) The
  claim carries the whole send recipe — investigation, conversation, author,
  lane, the `_MessageBody` as JSON — because of `_MessageBody` only `content`
  and `answers` reach the persisted `Message`; `apply_skills`, `image_paths`,
  `reasoning_effort`, `enhancements`, the search caps and `disclosure` would
  otherwise be lost and the re-run would answer a different question. NOT on
  it: the request-composed env (#714) — see round 1. (3) Reclaim rule, per
  KEY (one conversation's queue): its `released` claims (a SIGTERM handover)
  are taken now; its other claims are left while the key's heartbeat is fresh
  or ANY of them was opened or taken (`taken_at_ms`) within the stale window
  (no row yet is not a dead owner — round 3), and taken once it is stale (the
  owner died or stalled), on the same tick as the released ones (round 2); the epoch is
  advanced ONCE, before any re-run, when an unreleased claim was taken or
  given up on; re-runs go in the order asked. The
  claim is the ledger and the ONLY evidence of "owed": the first version also
  read the thread ("stale but the thread moved on ⇒ delete the claim") and
  round 1 found that rule dropping an owed claim in every ordinary shape — a
  queued Q1/Q2, the #624 notice after the question, a follow-up answered by
  another pod. What bounds re-runs instead is `RECLAIM_MAX_RERUNS` (2): past
  it the claim is finished and the thread gets an error ending that says so.
  `rerun` is `_send` minus the append and minus the live `UserMessage`
  publish; it returns once the turn is queued (a tick waiting on each reply
  would start the Nth orphan N × 25 s late), slices history at the claimed
  message (found by timestamp + text, not assumed last), and asks the seam's
  `env_without_request` for the env, as a goal-driven round does. (4) A handover
  does NOT persist the partial reply and the cancel marker (that is Stop's
  meaning); the peer's answer follows the question cleanly, and a
  single-replica deploy's restarted pod takes back its own released claims.
  So the phases are: **P3** claims + rerun + reclaim sweeper (the OOM path,
  via staleness); **P4** SIGTERM handover (release + cancel without persist,
  inside the drain budget); **P5** k8s / docs / ledger.

## 判準 (each one a probe through the real door)

- P1: with a describer that blocks for two or three 150 ms calls, the loop-lag witness records
  < 100 ms lag during `read_image` / `read_page` / the plot review; on the
  unfixed code it records ≥ 300 ms (each test reddens there). The same
  witness for `answer_doc_question` with a 300 ms formatter.
- P2: real app, one SSE held open, `kill -TERM`: the SSE client gets HTTP 200
  and EOF (not a reset); `lifespan: shutdown complete` is logged within
  `shutdown_budget_sec`; the process exits 143. `/api/readyz` answers 503 from
  the first moment of the drain. A turn that finishes inside the budget
  persists as before (`tests/api/test_turn_resilience.py`, the pre-existing
  `aclose` tests, still green); one that cannot is handed over (P4's tests).
- P3: two `create_app` pods in one process over one disk backend (the shape
  production has: one registry per process, one store between them; the
  in-memory event bus): pod A accepts a message and STALLS mid-turn (B's
  clock runs ahead, since a wedged loop cannot be staged) → B's tick takes
  the claim, advances the epoch, answers; A's copy is cancelled by the epoch
  and persists NOTHING, broadcasts nothing; the thread holds ONE answer. The
  OOM path proper (a dead owner, stale heartbeat, the thread still owed) is
  the same decision on a hand-written orphan claim. `kill -TERM`: A's own
  shutdown (its TestClient's lifespan) releases what it could not finish and
  B's sweeper takes it on its next tick.

## Deploy notes (to be carried into the PR body)

- `rca-app`: `terminationGracePeriodSeconds`, `preStop` — both new.
- `server.shutdown_budget_sec` (uvicorn's `timeout_graceful_shutdown` is the
  same number) and `server.turn_reclaim_interval_sec` — new knobs, an entry
  in `docs/migrations.md` (the upgrade runbook, `#pr-815`; master turned the
  ledger table into that runbook while this PR was open). The pod's exit takes at most: uvicorn's wait
  (≤ budget) + the lifespan drain (one deadline for every engine and, all-in-
  one only, the coordinators: ≤ budget, plus 4 s for each engine that still
  has turns past it — 2 s for the handover write, 2 s for the cancelled
  turns' teardown — so ≤ budget + 8 s with the two engines) + the teardown
  (kernels, the sandboxes' write-back — unbounded, seconds normally).
  `preStop`'s 5 s counts inside `terminationGracePeriodSeconds` too. With the
  defaults: 20 + 28 + 5 = 53 s before the teardown; the base's 90 leaves it
  37 s (60 left 7 s, and the first version said "2 × budget" as if the grace
  periods and the second engine did not exist).
- The reclaim sweeper is NOT a pure producer in the #804 sense (it runs the
  turn on its own engine, like `goal_offhours_sweeper`); it is the third
  stated exception in CLAUDE.md: the work it produces is a turn, which only
  an API pod can run — so it stays on the API, one pod per window, and lists
  a table bounded by in-flight turns.

## 執行結果

Branch `graceful-shutdown` off master `2ccfff9f`: P1–P4 as four commits, then P5
(docs / config example / ledger; the k8s side went in with P2).

| claim | how it was checked |
|---|---|
| `read_image` / `read_page` / the plot review no longer hold the loop | three loop-lag witnesses (`tests/agent/test_read_image_tool.py`, `tests/kb/test_read_tools.py`, `tests/agent/test_plot_review.py`): 451 / 309 / 301 ms of lag on the unfixed code, < 100 ms fixed |
| the tool-log sink wakes an idle loop from a worker thread | `tests/api/test_litellm_runner.py::test_tool_log_emitter_wakes_an_idle_loop_from_a_worker_thread` — latency-based; **mutation** (bare `put_nowait`): the chunk lands after 2.01 s (the test's own timeout timer), fixed: 0.1 s |
| SIGTERM begins the drain: readiness 503, every chat + monitor stream ended, uvicorn's wait ends, the lifespan runs | `tests/api/test_drain.py` (7) + `scripts/check_sigterm_drain.sh` on the real app: `drain: begun` → `shutdown complete` in 16–20 s across four runs (16.9 / 16.2 / 16.7 / 19.7 — the all-in-one index queue, drained to the budget; production is a pure producer) → exit 143, the SSE client gets HTTP 200 + EOF. **Mutation**: dropping `drain.on_begin(turn_engine.close_all_streams)` reddens the chat-stream test cleanly (the test has a structural exit so a hang becomes a failure) |
| the coordinator drain shares the budget | `test_the_coordinator_drain_is_bounded_by_the_same_budget`: a coordinator that never drains, budget 0.5 s, shutdown < 3 s (was 30 s) |
| a send opens a claim, the reply finishes it | `tests/api/test_turn_reclaim.py::test_a_send_opens_a_claim_and_the_reply_finishes_it` (the runner reads the store from inside the turn) |
| a peer re-runs the stored question without appending it | `…::test_rerun_answers_the_stored_question_without_appending_it` |
| the reclaim decision, per key: released → now (epoch untouched); fresh heartbeat, or any unreleased claim opened/taken within the window → leave; stale → take every claim on the key (released ones too), epoch once before any re-run — also on a give-up — re-run in order; a key is taken whole or left whole (a lost CAS ends the tick's work on it; a transient take error skips one claim); a claim owed whatever follows it in the thread; `RECLAIM_MAX_RERUNS` → one error ending, by whoever takes the spent claim, idempotent across ticks; a row finished between listing and taking is the turn ending | `tests/api/test_turn_reclaim.py`, the `# ── the reclaim decision` block: 20 tests through `ReclaimTick.of(app)` (21 distinct callers; the two-pod one is listed below) |
| two pods, one store: A stalls, B takes and answers, A's epoch-cancelled copy writes NOTHING and broadcasts no cancel | `…::test_two_pods_on_one_store_a_stalled_owner_is_taken_over_and_writes_nothing` — the first version reddened with `('error', 'The previous response was interrupted.')` AFTER B's answer (`is_mine` before persist is the fix); round 1's mutation (publish the cancel before persist decides) reddens it on `RunCancelled` |
| the lifespan sweeper does it on its own, behind the lease | `…::test_the_lifespan_sweeper_takes_over_a_released_turn_without_being_asked` (B's interval 0.1 s) + `…::test_no_sweeper_when_the_interval_is_none` |
| a draining pod hands over what it could not finish, without a partial or a marker | `…::test_a_pods_shutdown_hands_over_the_turn_it_could_not_finish` — A's shutdown through its own portal (budget 0.3 s), A's thread untouched, claim `released`, B's answer alone |
| the drain waits for a queued turn that starts DURING it while budget remains, and hands it over when it runs out | `…::test_a_pods_shutdown_waits_for_a_turn_that_started_during_the_drain` / `…::test_a_pods_shutdown_hands_over_the_queued_turn_that_started_during_the_drain` — the POSTs detach at once (`send_await_timeout=0.05`): with them still waiting, their preparation heartbeat sat in the first snapshot and waited the queued turn out by accident, so the snapshot-only mutation stayed green until that was fixed |
| the claim beats during preparation and while a re-run resolves its env; a re-run returns once queued; a re-run's history stops at the claimed message; a re-run gets the headless env, not a stored cookie | `…::test_a_claim_beats_while_its_turn_is_still_being_prepared`, `…::test_a_rerun_beats_while_resolving_the_headless_env`, `…::test_rerun_returns_once_the_turn_is_queued_not_answered`, `…::test_a_rerun_takes_its_history_from_before_the_claimed_question`, `…::test_a_rerun_runs_on_the_headless_env_not_on_a_stored_cookie` (`"caller_env" not in TurnClaim.__struct_fields__`) |
| `release` is one listing for the whole drain (a write per claim released), skips a peer's claim, is a CAS on what it listed; two opens in one ms are two claims; `take` counts the re-runs | `tests/api/test_turn_claims.py` (10) |
| shutdown keeps a sandbox the fleet is using (`kill_idle`'s rule), writes back what it keeps, forgets what it kills | `tests/api/test_registry.py::test_close_all_keeps_a_sandbox_the_fleet_is_still_using`, `…::test_close_all_writes_back_what_it_keeps_and_forgets_what_it_kills` |
| a send still preparing at the deadline is handed over, declined, persists nothing, broadcasts nothing | `…::test_a_send_still_preparing_at_the_deadline_is_handed_over_and_declined` |
| `answer_doc_question` keeps the loop free; `serve` exits 3 on a boot that never started; every engine drains against ONE deadline | `tests/api/test_doc_question_routes.py::test_answering_a_term_question_keeps_the_loop_free` (304 ms of lag on the unfixed code), `tests/test_main_serve.py`, `tests/api/test_drain.py::test_the_lifespan_drains_turns_against_one_deadline` |

Found on the way and fixed: `ChatTurnEngine.aclose` waited with
`wait_for(gather(*live), timeout)` — a timed-out `wait_for` cancels the gather
and the gather cancels its children, so the deadline was cancelling the turns
through Stop's persist path before the straggler list was computed. It uses
`asyncio.wait` now. Two `create_app`s cannot share one spec (the job models
refuse a second registration), so the two-pod tests use two specs over one disk
backend — which is production's shape.

Not done, deliberately: the FE. Read from the code (not seen in a browser): a
stream that ends mid-turn shows the #560 notice and a "may be missing a piece"
banner under the partial, the partial stays on screen, the peer's deltas are
appended to that same bubble, and the peer's `done` retracts the banner and
replaces the bubble with the stored answer. `TurnStatus`'s retry stays the
fallback. Whether that deserves a "taken over, restarting…" line of its own is
a UX call for later. Also not done: the KB chat (see P3's non-goal).

## Round 1 (four lenses in parallel, `f283225b`) — what changed

Findings that replaced a mechanism (each is pinned by a test whose mutation
reddens exactly it; the new tests outside `test_turn_reclaim.py` were also run
red on the round-0 code — that file imports the new bound and could not collect
there):

- `aclose` waited on a snapshot and handed over only if that snapshot had
  stragglers: a queued turn that started during the drain was cancelled
  through Stop's path with its claim finished. Now: a deadline loop over
  whatever is live, one handover for every conversation still running or
  queued, bounded by `_DRAIN_GRACE_S`.
- `registry.close_all` killed every session's sandbox unconditionally — not
  reached in a rollout with a stream open before P2, and under `kind: http`
  the sandbox is the fleet's one
  address-converged sandbox, the very one a peer takes the turn over into.
  Now `kill_idle`'s rule: write back, kill only when globally idle.
- The reclaim rule read the thread ("stale but moved on ⇒ drop the claim") and
  dropped owed claims on a queued Q1/Q2, the #624 notice, a peer-answered
  follow-up. Now the claim is the only evidence; per-key judgment; the epoch
  advanced once, before any re-run, and never on the released path (it was
  cancelling the taker's own first re-run, and bystanders); `RECLAIM_MAX_RERUNS`
  bounds the loop with an error ending the person can see.
- The request env (#714) was persisted on the claim — plaintext, for the turn's
  life, against `request_env.py`'s own contract. Removed; a re-run asks
  `env_without_request`.
- A superseded copy broadcast `RunCancelled` before persist decided it was not
  its turn; the engine now publishes the cancel only if `on_complete` did not
  answer `False`.
- Smaller: the claim beats during preparation (a cold sandbox wake no longer
  reads as a dead owner); `release` filters on owner and takes the whole list;
  claim ids carry a unique suffix (two sends in one ms were one row);
  `rerun` slices history at the claimed message and returns once queued;
  `take` on a row finished meanwhile is the turn ending, not an error;
  `answer_doc_question` off the loop; `serve` exits 3 when the server never
  started (`uvicorn.run` did; `Server.run` alone does not); ONE deadline for
  both engines and the coordinators; the live check probes `/api/readyz`
  (`/openapi.json` is 404 here, so it always waited its full 90 s).

Prose corrected with them: "never run in production" → "whenever a stream was
open at the signal" (the prod logs were never seen); readiness 503 is not what
stops traffic on a deletion; the FE behaviour above; the bound arithmetic
(`terminationGracePeriodSeconds` 60 → 90); the KB chat is not covered; P2 as
built; the 判準 as probed.

Mutation probes (file copy, restore; a throwaway script outside the tree):
15 mutations in round 1 (that runner was not preserved), and one runner kept
since round 2 whose entries are re-run at every head — its count and verdicts
at the final head are in the PR body — each reddening the test named for it — the
drain-snapshot one only after the P4 tests were made to detach their POSTs
(see the table). Not among the 15, and claimed as pinned anyway: `rerun`
returning once queued — round 2 caught the sentence and the pin is
`test_rerun_returns_once_the_turn_is_queued_not_answered` now.

Recorded here because round 2 asked where they were: `scripts/check_sigterm_
drain.sh` now reads the port from the config (the app has no override, so an
argument could only disagree with it) and fails loudly when readyz never
answers; `close_all(idle_after=None)` — a direct caller — kills only what no
pod has ever touched; a `rerun` whose preparation fails (the conversation is
gone, the recipe no longer validates) finishes the claim with an error ending
rather than being re-taken every tick.

## Round 2 (four lenses on `ce4476ce`) — what changed

Round 1's replacements, read as new code:

- A send still PREPARING at the deadline (compaction, a cold sandbox wake —
  its claim open, its turn not yet a task) was neither handed over nor
  stopped: the preparation finished after the drain, enqueued, and the turn
  ran on the drained engine — at process exit persisting a partial with its
  claim finished as this pod's. Now `pending_turns` count as unfinished, the
  claim is released with the rest, and the tokens are marked as a Stop during
  preparation marks them, so the worker declines the turn and the declined
  copy persists nothing and broadcasts nothing.
- Two ticks that overlap at a lease-window boundary could split one key's
  claims (B takes Q1, C takes Q2): both advanced the epoch and the later
  advance cancelled the other's legitimate re-run through Stop's path — Q1
  lost. A key is taken whole or left whole: the first `take` to lose its CAS
  ends this tick's work on that key. A transient error on one `take` skips
  that claim, not the key. A stale key's released and unreleased claims are
  taken on the same tick (taking only the released one had the other wait for
  that re-run's beat to go stale). The epoch is advanced when an unreleased
  claim was taken OR given up on (a stalled owner's copy must stop either
  way). Giving up is done by whoever TAKES the spent claim (one ending, not
  one per tick) and the ending is idempotent across ticks when `finish` is
  refused.
- `rerun` awaited `env_without_request` with the claim taken and no beat
  behind it — a slow policy read as a dead owner to the next tick, which took
  the claim again. The resolver now runs inside the preparation window,
  under its beat (`_start_turn(resolve_env=…)`).
- The bounded handover could time out while its thread's release was still
  in flight; round 2 added a deadline to the release for a scenario round 3
  then showed cannot occur (see Round 3) — removed again.
- `release` is a CAS on the etag it listed (a peer's `take` in between is
  not overwritten); `close_all` forgets the heartbeat of what it kills, as
  `kill_idle` does; the declined-turn path publishes its cancel only if the
  copy was still this pod's.

Prose: the ledger row back to the table's four cells (then, on merging master,
rewritten as a `#pr-815` runbook entry — #816 replaced the table); the ms-window duplicate
answer and the `kind: local` dir retention stated as trade-offs; the 503 —
round 1 had credited it with the non-deletion SIGTERMs, and uvicorn closes
the listener within 0.1 s of any SIGTERM, so it is the truthful answer and
not what stops traffic (`drain.py`, `deployment.md`, `deployment.yaml`, P2
as built); "a VLM thread delays the exit" was false — uvicorn re-raises the
captured SIGTERM under the default handler and the process dies at once, the
thread with it; the KB exclusion on the three sentences that still said
"every send" / "a turn survives its pod"; the `registry.close_all`
docstring's false "single-process deploy has no heartbeat store"
(`create_app` always wires one); "never ran before P2" in `registry.py`,
`test_registry.py`, `test_idle_kill.py` and the Round 1 section reworded to
"did not run in a rollout with a stream open"; the re-run named as an asker in
`IRequestEnv.env_without_request`, `_resolve_request_env` and
`docs/plan-headless-env.md` (the contract text had said only `driven_by`
callers may ask); "pure producer" for the reclaim sweeper corrected to the
third stated exception (plan, `lifecycle.py`; CLAUDE.md now names all three
in one sentence); `_open_orphan` really opens as pod A (`open` stamps the
opener — the stale-path tests had been taking over their own pod's claims);
`release` is one listing plus a write per claim, not "one round trip"; the
lifecycle's bound comment says per engine; 判準 P1's "3 × 150 ms" is two or
three calls; the live-check figure is four runs, not one number; the
gates sentence names the exact file list its count comes from.

Known limitations, stated rather than built around (Low, both): a Stop
pressed while a message was still being prepared, followed by a drain before
its turn ran, is lost — the peer re-runs the stopped question (the Stop lives
on an in-memory token); and the reply persist is get→append→update with no
CAS, so two pods persisting into one conversation at once (the peer's re-run
and a follow-up on the pod the user reconnected to) can overwrite each other
— before #815 that needed two users or failed sticky routing, now one user
and one rollout can produce it.

## As-built decision tables (written before round 4, from the code at `806bab2d`)

Four rounds each found real defects, and every one of them was an adjacent
cell of the same table — the fix each time covered the reported cell. So the
tables are written out here, every cell, and the tests are named per cell;
a cell without a test is a gap to close, not a note.

### A. `ReclaimTick._take_key`, per key (claims sorted by `(created_at, id)`, the same on every pod)

Inputs: `R` = the key's released claims, `U` = the unreleased ones;
`quiet` = every `u ∈ U` has `max(created_at, taken_at_ms) ≤ now − 30 s`;
`alive` = a heartbeat on the key within 30 s.

| cell | candidates | epoch | outcome | pinned by |
|---|---|---|---|---|
| `U = ∅` (only released) | `R`, in order | no advance | each taken (CAS) and re-run in order | `test_a_released_claim_is_taken_and_run_at_once`, `test_taking_a_released_claim_leaves_the_epoch_alone` |
| `U ≠ ∅`, some `u` fresh (opened or taken < 30 s ago) | `R` only | no advance | `U` left: a turn that has not had time to beat, or a peer mid-take | `test_a_claim_younger_than_the_stale_window_is_not_an_orphan`, `test_a_key_a_peer_took_moments_ago_is_left_whole_even_when_listed_after_the_take` |
| `U ≠ ∅`, quiet, alive | `R` only | no advance | `U` left: someone is driving the key | `test_a_claim_whose_owner_still_beats_is_left_alone` |
| `U ≠ ∅`, quiet, not alive | `R ∪ U`, in order | once, iff an unreleased claim was taken or given up, before any re-run | all taken and re-run in order | `test_a_stale_claim_with_the_question_still_owed_is_taken`, `test_a_stale_claim_is_owed_whatever_the_thread_says_after_it`, `test_two_stale_claims_on_one_key_are_taken_together_in_order`, `test_a_stale_key_takes_its_released_and_unreleased_claims_together`, `test_taking_a_claim_advances_the_epoch_so_a_stalled_owner_stops`, `test_giving_up_still_advances_the_epoch_on_a_stalled_owner` |
| any candidate: first `take` loses its CAS | — | as above for what was taken before it | `break`: the rest of the key is the peer's | `test_a_key_a_peer_is_taking_is_left_whole` (loss on the first claim) |
| any candidate: `take` → NotFound / Deleted | — | — | `continue`: the turn ended meanwhile | `test_a_claim_finished_between_listing_and_taking_is_the_turn_ending` |
| any candidate: `take` raises otherwise | — | — | `continue`: that claim's problem, next tick | `test_a_take_that_raises_skips_that_claim_and_still_runs_the_rest` (error on the FIRST claim) |
| taken and `reruns ≥ 2` | — | counts as unreleased-taken for the advance | give up: one error ending, idempotent after the thread moved on — and when the claimed message itself was undone (the search falls back to everything stamped later than the claim, not a positional slice; round 4) | `test_a_claim_rerun_too_often_ends_the_thread_with_an_error_instead`, `test_only_the_tick_that_takes_a_spent_claim_writes_its_ending`, `test_an_abandon_whose_finish_fails_writes_one_ending_not_one_per_tick`, `test_an_abandon_does_not_repeat_its_ending_after_the_thread_moved_on`, `test_an_abandon_finds_its_standing_ending_when_the_claimed_message_was_undone` |
| a re-run raises before its turn exists (driven claim's re-raise, seam down) | — | — | caught in `rerun`; the thread has its ending; the tick's other claims still run | `test_a_driven_claims_failed_rerun_does_not_cost_the_keys_other_claims` |
| CAS loss on a LATER claim after earlier successes | — | as above | `break` with `mine ≠ ∅`: earlier ones re-run; the later one is the peer's — a split, if the peer holds it live. Reachable only when the peer skipped the earlier claim (a transient error on its `take`) or read the heartbeat on the other side of its expiry — stated in the module docstring, not pinned | — (rare by construction; see the caveats) |

### B. `ChatTurnEngine.aclose` at the deadline, per workspace session (handover wired by the lifespan)

| session state | waited? | handed over? | what the copy writes | broadcast | pinned by |
|---|---|---|---|---|---|
| a turn running | yes, to the deadline (re-snapshot each pass) | yes | nothing (`persist` asks `is_mine` → released) | no cancel | `test_a_pods_shutdown_hands_over_the_turn_it_could_not_finish` |
| a turn finishing inside the budget | yes | no (nothing unfinished) | its reply, as always | as always | `test_a_pods_shutdown_waits_for_a_turn_that_started_during_the_drain` |
| queued behind a turn that ends inside the budget | the worker starts it; waited while time remains | if it cannot finish | nothing | no cancel | `test_a_pods_shutdown_hands_over_the_queued_turn_that_started_during_the_drain` |
| still queued when the workers are cancelled | — | yes (its key is unfinished) | its item stays in the queue; the worker is gone, but any `enqueue` on that key during the teardown respawns one, which runs the old items first — their claims are released, so they persist nothing (wasted work, not a wrong write); a KB queued turn revived this way persists normally | — | stated (round 4); not pinned |
| a send still preparing (token registered, no turn yet) | its beat keeps it live, to the deadline | yes; the token marked AFTER the release | the worker declines the turn when it enqueues; nothing persisted | no cancel | `test_a_send_still_preparing_at_the_deadline_is_handed_over_and_declined` |
| … and that preparation then FAILS on the dying pod | — | (already) | nothing (`_end_with_failure` asks `is_mine`) | no `RunError` | `test_a_handed_over_sends_late_preparation_failure_writes_nothing` |
| … and it was a DRIVEN send (a goal follow-up) that fails after the handover | — | (already) | nothing; and the driver is NOT told "this round did not start" — the round starts on the peer (round 4: the throw had the driver refund a round the peer then ran) | no `RunError` | `test_a_handed_over_driven_sends_late_failure_is_not_reported_to_its_driver` |
| a claim the tick TOOK but had not yet started re-running when the sweeper was cancelled (the ms between `take` / `advance` and the first `rerun`) | — | no: no token exists yet for the drain to see | nothing (nothing ran) — the claim stays this pod's until the stale window; once ONE re-run on the key is preparing, the handover's `release(keys)` covers the other taken claims on that key too | — | stated (round 5); a ms window, not pinned |
| a RE-RUN the sweeper started, still preparing when the pod drains | its beat keeps it live | yes — the re-run is its own held task (`_inflight`), so it survives the sweeper's cancel and its token is there for the drain (round 4: inside the sweeper's task it died with it, token gone, claim left this pod's for the stale window) | nothing | no cancel | `test_a_re_run_still_preparing_when_its_pod_drains_is_handed_over_too` |
| no handover wired (a caller without a claim store) | yes | — | the partial + "interrupted", as Stop does | cancel | the pre-existing `aclose` tests (`test_turn_resilience.py`) |
| the handover's release runs late (past the 2 s grace) | — | the thread finishes on its own | no answer lost or duplicated: a copy that persisted has FINISHED its claim (the late write is a no-op on a deleted row); one whose persist failed, or a queued turn never started, is thereby handed over; a claim a peer took fails the CAS. The one window — the release landing between a copy's `is_mine` read and its `finish` (a few ms: one `conv_rm.update`), with a peer's tick inside it — costs that peer one wasted re-run; the copy's own reply stands | — | by enumeration (round 3) and interleaving (round 4, measured 0.3–6 ms) |
| KB engine sessions (no claims) | as above | release finds nothing | running → partial + marker; queued → dropped, nothing persisted; preparing in the route → not tracked, runs after the drain, dies at exit | cancel | `docs/deployment.md` states it; the pre-existing KB drain behaviour |

### C. Every writer that must ask `is_mine` first

`persist` (the reply), `_end_with_failure` (a failed preparation, a failed
re-run, a give-up — via `abandon`), the worker's declined-turn path (through
`persist`). Two writers during preparation do NOT ask and are left as they
are: `compact` (the #739 summary) and `_notice_history_reduced` (the #624
notice) — both idempotent for the peer's re-run (`already_noticed`; a second
compaction is the no-CAS persist limitation already stated).

## Round 4 (four lenses on `806bab2d`) — the tables' missing cells

Asked for by the user ("不用review了嗎" — P8 replaced a mechanism and removed
one; the budget is not a reason to skip the round that follows that). Built
the other way round this time: the tables above were written first, then
every finding was placed in its cell, and each cell got its test before the
fix.

- Table B was missing "a re-run the sweeper started, still preparing at the
  drain": the sweeper is a background task the lifespan cancels BEFORE the
  engine drains, and a re-run that lived inside it died with it — token
  gone, nothing for the drain to hand over, the claim left this pod's for
  the stale window plus a rerun count. `rerun` runs the preparation as its
  own task, held in `_inflight` like a send's (a `shield` alone keeps only a
  weak reference), so the drain finds its token and releases the claim.
- Table C was missing the driven cell of the failure path: a goal-driven
  send handed over while preparing, then failing on the dying pod, recorded
  nothing (right) but still threw to its driver, which reads a throw as
  "this round did not start" and refunded a round the peer then ran.
  `_end_with_failure` returns whether it recorded; the throw is for a
  recorded failure only.
- `abandon`'s fallback (the claimed message undone) sliced positionally
  past a filtered prefix and skipped the standing ending — one more give-up
  ending per tick while `finish` was refused. `_messages_after` returns
  what follows the message when found, else everything stamped later.
- Prose: "harmless in every case" for a late release → "loses or duplicates
  no answer; two windows: a peer's wasted re-run in the ms between `is_mine`
  and `finish` (round 4 measured 0.3–6 ms), and a finish-refused claim's
  re-run brought forward"; a refused release write is logged (it was
  silently suppressed); the split-key caveat's real precondition (two ticks
  that both list before either takes, reading the heartbeat on either side
  of its expiry, the unreleased claim sorting first); the age rule's
  quantifier ("any unreleased claim opened or taken within the window", the
  plan had said "the oldest"); the preparing-send test observes the token,
  not a deadline (that sentence had outlived the deadline's removal); the
  counts re-derived (`test_turn_claims.py` 10; the 24-file set had 479 before
  P8, not 483; one test was removed with the deadline, not two).
- CI on `806bab2d` (the run this round replaced) was RED in the `rest` job,
  not a flake: `tests/config/test_server_settings_are_documented.py` guards
  that every `server.*` knob is named in `docs/configuration.md`, and the two
  new knobs were not — an operator could not have discovered them from the
  manual the runbook points at. Both are in the manual's "what to set for
  what" table now. The guard is in a job my targeted set never included;
  the set is the files touching the changed seams, and a docs guard is not
  one of them — so the CI round is what catches this class, as intended.
- The mutation runner is the ledger: 24 entries at this head, each
  reddening the test named for it (`r2_mutations.py`, kept outside the tree
  since round 2 and re-pointed as the text moved). Two entries had HUNG under
  their mutation rather than reddened: a tick that takes leaves a live turn
  on the test loop, and the `with` exit then waits on it for ever — those
  tests settle the engine before leaving (`_settle_engine`), so the mutant
  is red, not a hang. "Give up without taking first" is pinned by
  `test_a_claim_rerun_too_often_ends_the_thread_with_an_error_instead`
  (without the take, `is_mine` says the claim is not ours and no ending is
  written), not by the spent-claim test round 2 named — `is_mine` masks it
  there.

## Round 5 (one question, regression lens on `806bab2d..d1fb5f21`)

P9 replaced three mechanisms; the round asked only whether any input the
OLD ones handled is handled worse. None is: `rerun` as a held task
sequences a key's re-runs at the same point (once enqueued), survives the
sweeper's cancel and is drained like a preparing send; `_messages_after`
equals the old slice in every cell where the claimed message is present and
fixes the four cells where it is absent (a 32-cell parity probe, and the
same 16 cells through the real tick with the old file swapped in: 4
failed); the driven throw's six cells (claim none/mine/taken × driven or
not) are unchanged except the one round 4 fixed. Two things stated rather
than built: the table-B row above (a claim taken but not yet re-running
when the sweeper is cancelled — a ms window), and a cosmetic log line —
when the sweeper is cancelled inside the `shield` and the held re-run
raises later, CPython's `shield` leaves the inner exception unretrieved
("Task exception was never retrieved" at GC); the failure itself was
already logged inside `_start_turn`, and `send`'s shield has had the same
property since it was written.

## Round 3 (four lenses on `7031392c`) — the last of the budget

Fixes small enough not to buy another round (a guard, a catch, a scan, a
log line — each with a test that reddens on `7031392c` and a mutation that
reddens exactly it):

- `_end_with_failure` recorded a failure without asking `is_mine`: a send
  handed over while preparing keeps preparing on the dying pod, and when
  that raised (a sandbox gone under it) the pod ended the thread with its own
  exception and deleted the PEER's claim — the peer's answer then found
  nothing to finish and was dropped. The failure path now asks the same
  question `persist` does.
- A goal-driven claim's re-run failure re-raised out of `rerun` (the first
  run re-raises so the driver learns of it) and aborted the tick's key loop
  after taking the other claims. `rerun` catches it: the ending is written,
  nobody is waiting for the throw.
- `abandon`'s idempotence looked only at the LAST message; a person writing
  between ticks got one more give-up ending per tick while `finish` was
  refused. It now looks at everything after the claimed message.
- `close_all` logged a refused `forget` as "left item behind (teardown
  failed)" though the sandbox was gone; its own line now.
- A claim with no heartbeat row yet read as stale: between `open` and the
  first beat a tick (the pod's own, or a peer's) took the claim over and the
  turn ran twice — `test_headless_env` in the targeted set reproduced it (one
  failure in 479). The same gap sits between a peer's `take` and ITS first
  beat: a tick listing in it took the claim back from the peer, and the
  peer's later take of the next claim had both ticks advancing the epoch —
  the split "taken whole or left whole" was meant to rule out. One rule for
  both: `take` stamps `taken_at_ms`, and a key is stale only when its
  heartbeat is silent AND no claim on it was opened or taken within the
  stale window (`ReclaimTick.now_ms` is the clock it is judged by).
- `release(keys, not_after=…)` from round 2 is gone. Round 3 enumerated what
  a late release can meet: a copy that persisted has FINISHED its claim (the
  row is deleted; the write is a no-op), a copy whose persist failed or a
  queued turn never started is thereby handed to a peer (wanted), a claim a
  peer took fails the CAS. The scenario the deadline was built for
  ("partial persisted as mine, then the late release has a peer re-run it")
  cannot occur — `persist` deletes the claim right after the marker — and
  the deadline's only real effect was to make a queued claim on a slow store
  wait the 30 s stale window instead of a tick. A guard that guards nothing
  goes, with its store-level test (the preparing-send test that also used it
  was reworked and kept).

Pins round 3 found idle, fixed: the "transient take error skips the claim"
test raised on the key's LAST claim, where `continue` and `break` look alike
— it raises on the first now; the preparing-send test now observes, at the
handover, the token still unmarked (marked before the release, the worker
would decline with the claim still this pod's). The three inline `open(…, owner="pod-a")`
that `open` was silently re-stamping go through a pod-a store like
`_open_orphan`.

Stated in the reclaimer's docstring rather than solved (Low, rare): two
overlapping ticks that both list BEFORE either takes and read the heartbeat
on either side of its expiry compute different candidate lists and can
still split a key when the unreleased claim sorts before the released one
(one re-run cancelled as "interrupted"; `taken_at_ms` protects a tick that
lists after a take, not two that listed first); `take`'s CAS is specstar's check-then-set, so
two takes inside one millisecond both "win" (the last writer owns, the other
copy's `is_mine` is False). And a default executor saturated by VLM threads
can keep the handover's release from starting inside its 2 s, in which case
the drain falls back to the pre-P4 behaviour — an ERROR line with traceback
("could not hand over … they persist as cancelled"), and the partial persists
unless the late release lands before the cancelled copy asks `is_mine`, in
which case that copy writes nothing and a peer re-runs.

Gates: `ruff check` / `ruff format --check` / `ty check` clean; `mkdocs build
--strict` exit 0; targeted set (the 24 test files touching the changed seams —
turns, claims, reclaim, drain, registry, idle kill, cross-pod cancel, stop,
send detach, messages, request env, KB chat queue, event bus, presence, item
resources, the runner, doc questions, serve) green: 484 passed in 261 s on the final tree (the PR body names the files).
