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
| P7 | `docs/migrations.md`: retry behaviour changes with no knob (and a schema bump if the persisted field needs one). |

## Risks

- **P2 context trimming moves.** The whole-turn retry trims conversation
  HISTORY and re-runs; per call, the input also holds this turn's own tool
  results. The termination guarantee (#624: every round strictly smaller in
  tokens) has to move with it, and be pinned by a test that reddens on the
  unfixed code.
- **P2 removes a mechanism**, so per the review budget rule it is new code and
  earns its own review round.
