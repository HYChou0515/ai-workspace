# Plan — errors a user can read, with the raw text one click away

## Problem

When a turn fails, the user is usually handed the provider's raw exception
string. The case that started this: a **429 that arrives after the turn has
already streamed** (e.g. the agent's second model call, after a tool ran). The
user sees `RateLimitError: litellm.RateLimitError: …`, or on a fallback chain
`AllProvidersFailed: all agent models failed or were cooling` — nothing about
what happened or what to do.

Read from `origin/master` at `d0752e6c` (not measured live):

- **Retry is whole-turn.** `LitellmAgentRunner.run`
  (`api/litellm_runner.py`, the `while True` around `_run_once`) re-runs the
  ENTIRE agent run on failure. Because that would repeat tools already
  executed, it refuses once anything visible has streamed (`progress_made`,
  #26). So a mid-turn 429, timeout, connection drop or 5xx can only fail.
- **Only a chain of ≥2 endpoints retries per call.** `failover/model.py`
  `FallbackModel` wraps each model call and waits out a 429 there (#742), but
  a single-endpoint preset gets a bare `LitellmModel` (`litellm_runner.py`
  `_build_model`) with no per-call handling at all.
- **Raw text everywhere else.** Give-ups are `giving up after N attempts:
  Type: exc` / `Type: exc`; `turns.py:_terminal_error` falls back to
  `f"{type(exc).__name__}: {exc}"`. Only `rate_limited` / `all_busy` /
  `cancelled` (+ `max_turns`) have worded text in the FE
  (`agentLog.ts:errorTextFor`). Two zh-TW sentences are hard-coded in the
  runner (429 give-up, context overflow) with no `kind`.
- **Recovered retries persist as errors.** Every `RunError`, including the
  non-terminal `retry: …` notice, is persisted `role="error"` by
  `_TurnReducer`, so a turn that recovered still shows a red banner on reload,
  and `answer_question` / `run_agent_task` report a sub-agent as failed when
  its last `RunError` was only a retry notice.
- **Sub-agent holds are invisible.** `agent_progress.py` forwards tool and
  reasoning progress only; a held sub-agent looks like a stuck tool card.
- **Other surfaces** show raw text too: workflow `result.error = "Type: exc"`,
  `SourceDoc.status_detail = "Type: exc"[:240]`, our own API's non-2xx as
  `send failed: 500`.
- **No expandable raw-error UI exists** (only the archive import's
  `<details>` "Why (N)").

## Locked decisions

Source of each: **user** = decided by the user in the 2026-10-12 /grill-me;
**proposed** = mine, listed for the user to overturn, not yet confirmed.

| # | Decision | Source |
|---|---|---|
| D1 | Every error is shown in three parts: **what happened**, **what you can do**, and **technical details** (the raw text). The details are visible to **everyone**, collapsed by default, with a copy button. | user (Q2) |
| D2 | **Retries happen only at the single LLM API call.** The whole-turn retry is removed. A single-endpoint preset gets the same per-call wrapper a chain has. | user |
| D3 | A call whose stream **breaks after it has shown text** is re-sent; the partial text of THAT call is withdrawn and replaced. Earlier calls' text and tool results stay. | user (Q3) |
| D4 | **Wait budgets are out of scope.** Single endpoint keeps its 120 s; chains keep `rate_limit_budget_s`. Only the place the wait happens moves. | user (Q4: "離題") |
| D5 | The copy, per `kind` (English translates the same meaning): see the table below. | user (Q4) |
| D6 | Backend classifies (`failover.classify.classify_error(exc) → kind`). `RunError.message` / the persisted error `content` IS the raw text (every link of the cause chain, `raw_error_text`) and is what technical details show; the FE words the failure from `kind` via i18n. The numbers the copy needs ride `RunError.params` → `error_params`. Every failure carries a kind; one the emitter could not name is `unknown`. (Amended in P1: a separate `raw` field would have duplicated `content`, which errors never replay to the model anyway.) | proposed, accepted with the plan |
| D7 | Error rows persisted before this have no `kind`: shown as `unknown`, their old text under technical details. | proposed |
| D8 | One FE component, `ErrorNotice`, for every surface: chat error box, persisted banner, KB chat, sub-agent tool card, workflow, document index status, our own API's non-2xx. | proposed |
| D9 | A retry in progress is an ephemeral event (status line "連線不穩，正在重試（第 N 次）…"), never persisted — which also fixes recovered retries persisting as errors and sub-agents reported as failed. | proposed |
| D10 | Sub-agent holds / retries are forwarded to the parent stream. The tool card shows the worded error; the tool RESULT given to the LLM keeps the raw text (a model decides its next step better from it). | proposed |

### D5 copy

| kind | 發生了什麼 | 你可以怎麼做 |
|---|---|---|
| `rate_limited` | 模型服務限制了請求頻率，已自動等了約 N 秒仍未解除 | 過幾分鐘再送一次；常發生請管理員提高額度或加備援模型 |
| `all_busy` | 所有可用的模型都在忙 | 稍後再送一次 |
| `auth` | 模型服務不接受目前的憑證 | 請管理員檢查模型的 API key 或帳號設定 |
| `timeout` | 模型太久沒有回應，已重試 N 次 | 稍後再試，或換一個較快的模型 |
| `connection` | 連不上模型服務 | 可能是網路暫時中斷，稍後再試；持續發生請聯絡管理員 |
| `provider_error` (5xx) | 模型服務本身出錯 | 稍後再試；持續發生請聯絡管理員 |
| `context_overflow` | 內容超過模型一次能讀的範圍 | 把訊息拆小，或開新對話（the current sentence） |
| `bad_request` (400) | 模型服務不接受這次的請求，通常是設定或模型不支援某功能 | 把技術細節複製給管理員 |
| `unknown` | 發生了預期外的錯誤 | 把技術細節複製給管理員 |

`cancelled` / `max_turns` keep their copy and gain technical details. A turn
that fails after producing output keeps that output; the notice follows it.

## Phases

| Phase | Work |
|---|---|
| P1 | Backend: `classify_error`, `RunError.kind/raw`, the persisted error field, `events.ts` mirror. |
| P2 | Per-call wrapper for every endpoint: 429 hold, transient re-send (timeout / connection / 5xx), garbled tool-call JSON re-sent with a hint (#76), context overflow trimmed on THIS call's input (#624); the "withdraw partial output" event (D3); remove the whole-turn retry. |
| P3 | FE `ErrorNotice` + i18n copy; wired into app chat and KB chat. |
| P4 | Sub-agents: forward holds / retries, worded failure on the tool card. |
| P5 | Workflow and background-job errors. |
| P6 | Our own API's non-2xx. |
| P7 | `docs/migrations.md`: retry behaviour changes with no knob; one new index with no backfill. |

## What the build changed, and why (P1–P6)

Recorded as they were decided while building; each names its evidence.

- **D6 amended in P1** — no separate `raw` field: the message / persisted
  `content` is the raw text (every cause-chain link). See D6 above.
- **The tool-arg retry moved in-band (P2).** With no whole-turn retry, a
  non-object or concatenated tool-call argument is answered in-band by
  `args_recovery` and the model corrects itself next call. Safe because
  `RepairingModel` now sanitizes the whole-response path too
  (`test_a_whole_response_is_repaired_too`).
- **`cancelled` / `max_turns` gained no technical details (P3).** Their stored
  text is an explanatory sentence, not something to debug from; there is
  nothing to open.
- **`failure.rate_limited.what` (no count) is neutral (P5).** The first draft
  said "this reply did not finish", which is wrong for a document; the counted
  variant (D5) is unchanged. A document's remedy is its own
  (`kb.doc.failedAction`, re-read it), not a chat's ("send it again").
- **P4 covers `ask_knowledge_base`, `run_agent` and the skill-hub review.**
  They hand their sub-agent a `Delegation` (`agent/delegation.py`): the card
  sink every wrapper already forwards, plus the turn's status line and a way
  back to the calling tool call. `ask_wiki` (the KB agent's own wiki
  delegation) has its own relay and is not covered.
- **P5 covers document indexing and workflow runs.** A failed run records
  `error_kind`; a step's own failure is `step_failed` — a workflow-only kind
  outside `failover.classify.ERROR_KINDS` — whose `error_reason` (written for a
  reader by a gate or `fail()`) is the headline. KB archive-import errors are
  not covered: they already sit in a collapsed "Why (N)" list.
- **P6 copy is proposed, not from D5.** D5 worded the MODEL service; our own
  API's refusals needed their own: `failure.forbidden.*` (403) and
  `failure.server_error.*` (5xx). Every other status stays `unknown` with its
  status line as the details, rather than copy invented for statuses this app
  has not seen.

## Review round 1 (2026-10-12)

Four lenses (conformance, veracity, defect, regression) on `c0dd942d`. What
they found, and what changed — each fix built test-first, the test red on the
unfixed code, the fixed line then mutated to prove the test guards it:

- **Defect — a re-sent call's partial text survived outside the transcript.**
  Five callers joined `MessageDelta` text themselves (`drive_subagent`,
  `answer_question`, the wiki reader, the card drafter, compaction) and handed
  the model "half an the whole answer". They now read through
  `events.AnswerText`, which honours `OutputRetracted`; a source test keeps a
  sixth from joining by hand.
- **Defect — the rate-limit budget reset on every model call.** It is now one
  pool per `CallRetryModel`, i.e. per turn. The re-send COUNT stays per call,
  deliberately (a late blip is not charged against an early one; the turn is
  bounded by max_turns).
- **Defect — the inner stream was not closed on Stop** (`aclosing`); the FE
  withdrew by UTF-16 unit where the backend counted code points; a document
  failure with no kind lost its remedy; the copy button did nothing on a
  plain-http deploy (now absent there).
- **Regression — the reclaim give-up and the off-hours night note read worse.**
  The give-up is `turn_lost` ("send it again"); the night note carries its
  failure as `Message.failure` (the field `tool_failure` was renamed — it was
  unmerged) and the thread words it, while the note's text still ends on the
  raw words for text-only readers.
- **Regression — a parser's reason was hidden in the doc list** behind
  "unexpected error": an `unknown` kind falls back to the detail there.
- **D6 everywhere** — jobs, workflow runs and the findability answer keep the
  whole cause chain (`raw_error_text`) and a kind.
- **Recorded rather than changed:** D3 does not re-send a call once the SDK has
  recorded one of its items (`response.output_item.done`) — a re-send would add
  a second copy; a failover chain's own 429 hold is followed by up to 120 s
  more from the call wrapper (master stacked these only before output); retries
  are narrower than the old loop's — auth, bad requests, unknown errors and a
  failing credential resolver end the turn at once (`docs/migrations.md#pr-915`,
  `configuration.md` contract 3); the doc list shows a headline with the raw
  text on hover (the row is a button — no copy control fits) and
  `WriteFailureNotice` keeps its own headline over `FailureDetails`; copy beyond
  D5: count-less `timeout.what`, trailing 。, and the app's own kinds
  (`turn_lost`, `stop_failed`, `stream_dropped`). D5's `rate_limited.do` and
  `context_overflow.do` were restored to the approved wording. Chat export and
  video render a failure's `content` verbatim, i.e. the raw text — accepted.
- **Docs the in-app AI reads** (`subsystems/api-and-turns.md`,
  `subsystems/agent-runtime.md`, `architecture.md`, `glossary.md`,
  `decisions.md`) described the removed whole-turn retry and are updated.

## Review round 2 (2026-10-12)

Two lenses (defect + regression, veracity + conformance) on `076400a2`. The
defect lens re-ran every round-1 failure and found each one fixed. New:

- **Regression — an unnamed night failure hid its own words.** The off-hours
  note's usual cause is the turn gate refusing (a full workspace, a spent
  sandbox quota), named `unknown`; stripped and worded as "something
  unexpected", it said less than the sentence it replaced. A note whose failure
  is `unknown` now stays as written, the same rule the doc list follows.
- **Copy said what did not happen.** `failure.timeout.tried` counted every send
  as a RE-send ("已重試 3 次" after two re-sends): it is now "試了 N 次都沒能完成"
  (a deviation from D5's "已重試 N 次", for accuracy). A rate limit given up
  before any wait no longer reads "waited about 0 seconds".
- **The findability Try-answer** now words its failure (`ErrorNotice`) instead
  of printing the raw cause chain.
- **Recorded rather than changed:** the rate-limit pool is per `run()` — a
  delegated sub-agent or a compaction pass has its own (docstrings corrected);
  `aclosing` closes the stream the wrapper iterates, the provider's own stream
  is finalized by the loop's asyncgen hooks shortly after (comment corrected);
  a re-sent sub-agent call's withdrawn reasoning stays in the parent tool card's
  progress lines (cosmetic); the failover chain's `rate_limit_budget_s: 0` still
  gets the call wrapper's 120 s (`configuration.md` corrected);
  `ToolCallParseError` has no emitter (marked so in `events.py` and
  `contract.md`); older plans (`plan-repetition-guard.md`, `plan-issue-624.md`)
  mention `progress_made` as their own history and are left as written.

## Found recording the demo (2026-10-12)

Recording the screencast against an OpenAI-compatible stand-in, a 429 with
`Retry-After: 4` on the call after a tool showed "waiting for the model",
never the hold. The SSE stream carried no `rate_limited` at all, and the
stand-in saw the 429'd call re-sent about five seconds later: litellm builds
its OpenAI client with `max_retries=2`, and that client waited out the
Retry-After and re-sent on its own, so `CallRetryModel` never saw the failure.
D2 holds only if the wrapper is the one retry layer, so every call it makes
now carries `max_retries=0` / `num_retries=0`, merged into the call's own
`extra_args` (Ollama's `num_ctx` rides the same dict). Re-probed after the
fix: `rate_limited` arrives right after `tool_end`. The unit tests never saw
this because they replace `LitellmModel`; the new runner test goes through
the real one to the `acompletion` it calls.

## Risks

- **P2 context trimming moves.** The whole-turn retry trims conversation
  HISTORY and re-runs; per call, the input also holds this turn's own tool
  results. The termination guarantee (#624: every round strictly smaller in
  tokens) has to move with it, and be pinned by a test that reddens on the
  unfixed code.
- **P2 removes a mechanism**, so per the review budget rule it is new code and
  earns its own review round.
