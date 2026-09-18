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
  KEY (one conversation's queue): some claim `released` (a SIGTERM handover)
  ⇒ take those now; else the key's heartbeat fresh ⇒ leave every claim on it;
  else (stale — the owner died or stalled) ⇒ take every claim on the key,
  advance the epoch ONCE before any re-run, re-run in the order asked. The
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
| the reclaim decision, per key: released → now (epoch untouched); fresh heartbeat → leave; stale → take every claim on the key (released ones too), epoch once before any re-run — also on a give-up — re-run in order; a key is taken whole or left whole (a lost CAS ends the tick's work on it; a transient take error skips one claim); a claim owed whatever follows it in the thread; `RECLAIM_MAX_RERUNS` → one error ending, by whoever takes the spent claim, idempotent across ticks; a row finished between listing and taking is the turn ending | `tests/api/test_turn_reclaim.py`, the `# ── the reclaim decision` block: 15 tests through `ReclaimTick.of(app)` (the two-pod one is listed below) |
| two pods, one store: A stalls, B takes and answers, A's epoch-cancelled copy writes NOTHING and broadcasts no cancel | `…::test_two_pods_on_one_store_a_stalled_owner_is_taken_over_and_writes_nothing` — the first version reddened with `('error', 'The previous response was interrupted.')` AFTER B's answer (`is_mine` before persist is the fix); round 1's mutation (publish the cancel before persist decides) reddens it on `RunCancelled` |
| the lifespan sweeper does it on its own, behind the lease | `…::test_the_lifespan_sweeper_takes_over_a_released_turn_without_being_asked` (B's interval 0.1 s) + `…::test_no_sweeper_when_the_interval_is_none` |
| a draining pod hands over what it could not finish, without a partial or a marker | `…::test_a_pods_shutdown_hands_over_the_turn_it_could_not_finish` — A's shutdown through its own portal (budget 0.3 s), A's thread untouched, claim `released`, B's answer alone |
| the drain waits for a queued turn that starts DURING it while budget remains, and hands it over when it runs out | `…::test_a_pods_shutdown_waits_for_a_turn_that_started_during_the_drain` / `…::test_a_pods_shutdown_hands_over_the_queued_turn_that_started_during_the_drain` — the POSTs detach at once (`send_await_timeout=0.05`): with them still waiting, their preparation heartbeat sat in the first snapshot and waited the queued turn out by accident, so the snapshot-only mutation stayed green until that was fixed |
| the claim beats during preparation and while a re-run resolves its env; a re-run returns once queued; a re-run's history stops at the claimed message; a re-run gets the headless env, not a stored cookie | `…::test_a_claim_beats_while_its_turn_is_still_being_prepared`, `…::test_a_rerun_beats_while_resolving_the_headless_env`, `…::test_rerun_returns_once_the_turn_is_queued_not_answered`, `…::test_a_rerun_takes_its_history_from_before_the_claimed_question`, `…::test_a_rerun_runs_on_the_headless_env_not_on_a_stored_cookie` (`"caller_env" not in TurnClaim.__struct_fields__`) |
| `release` is one round trip for the whole drain, skips a peer's claim, is a CAS on what it listed and writes nothing past its deadline; two opens in one ms are two claims; `take` counts the re-runs | `tests/api/test_turn_claims.py` (11) |
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
15 mutations, each reddening exactly the test that pins it — the
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
  in flight: the cancel then persisted the partial as this pod's, and the
  late release had a peer re-run the question (Q, partial, "interrupted", A).
  `release(keys, not_after=…)` writes nothing past the drain's deadline.
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
(`create_app` always wires one); the re-run named as an asker in
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

Gates: `ruff check` / `ruff format --check` / `ty check` clean; `mkdocs build
--strict` exit 0; targeted set (the 23 test files touching the changed seams —
turns, claims, reclaim, drain, registry, idle kill, cross-pod cancel, stop,
send detach, messages, request env, KB chat queue, event bus, presence, item
resources, the runner, doc questions, serve) green — count in the PR body.
