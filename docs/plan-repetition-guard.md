# Plan — Repetition Guard (#113)

> Detect when an LLM degenerates into a **repetition loop** (repeating the same
> phrase / sentence / multi-sentence block until it never stops) and **block it
> live, gracefully** — without the worst-UX "hard terminate + no retry".
>
> **Flat integer phases** (`Phase 1`, `Phase 2`, …; never `1a`/`1b`) — each phase a
> shippable, testable increment driven by **`/tdd`** (red→green→refactor). Backend:
> pytest + 100% coverage on new modules; FE: vitest (FE follows `/tdd` too). Iterate
> with the changed-behaviour tests + ruff + ty; run the full suite + coverage gate
> once at the end of each phase.
>
> Phases have **no hard ordering dependency** — they were chosen for completeness,
> not sequence. P1 (detector) is the natural first brick because P2 consumes it.

---

## Background — why this, why now

- **The problem is real and not model-size-bound.** Observed live even on the hosted
  **Qwen3.5 397b**, not just small local models: the model keeps emitting the same
  text and never converges, burning tokens and stalling the turn. Academic name:
  **neural text degeneration** (Holtzman et al. 2020).
- **No framework gives this for free.** The OpenAI Agents SDK has `max_turns` (caps
  agent *iterations*, not in-token repetition) + a Guardrails system you'd have to
  write a custom validator for; **LiteLLM** is a pass-through gateway with transport
  retry only. Neither detects a content loop.
- **Sampling penalties are necessary but not sufficient.** `frequency_penalty` /
  `presence_penalty` / `repetition_penalty` work on **vLLM**, but **Ollama's newer Go
  runner (the path Qwen3.x runs on) silently drops them** — accepted then ignored. So
  a backend-independent **mid-stream detector** is the load-bearing layer; sampling is
  a cheap first line that may be a no-op on local Ollama.
- **Best-practice UX** (industry 3-layer framework): prevention > detect-and-stop >
  graceful truncate. **Hard-stop with no retry is the worst UX** — avoided here.

## Scope

- **In scope (v1):** repetition **within a single response** (one model generation /
  reasoning stream).
- **Out of scope (v2):** cross-step / cross-turn repetition (the model repeating the
  same message every agent step); **DRY** sampler and `no_repeat_ngram_size`
  (Ollama lacks them, vLLM needs a custom logits processor); mid-stream **retry** with
  bumped sampling params (conflicts with the existing `progress_made` retry gate, #26).

## The 3-layer design (locked via grill)

| Layer | What | Where |
|---|---|---|
| **L1 Prevention** | optional `frequency_penalty` / `presence_penalty` / `repetition_penalty` sampling params | `_agent_for()` → `ModelSettings` (`litellm_runner.py:286`) |
| **L2 Detection** | backend-independent mid-stream **period detector** on the content + reasoning deltas | new `agent/repetition.py`, fed from `produce()` (`litellm_runner.py:709-716`) |
| **L3 Graceful block** | stop generation, **keep the repeats on screen** (so the user sees the LLM misbehaved), truncate only the **persisted message + history** | `produce()` finalize + new SSE event |

### Locked decisions

- **L2 algorithm — tail-period detection.** Maintain the last `W` chars (default
  `4000`) of the **current response** in a buffer; on each delta, check only the
  buffer **tail** for a smallest period `p` (`1 ≤ p ≤ L_max`, default `800`) such that
  the tail is "a block of length `p` repeated `≥ R` times" (default `R=3`). Small `p`
  catches `the the the`; large `p` catches a repeated multi-sentence block.
- **Two independent detectors — content and reasoning.** Reasoning-channel loops are
  the worst token-burners (the hosted Qwen often loops in *thinking*), so the
  reasoning stream gets its own detector. A pure-reasoning loop that never emits
  content/tool is still caught.
- **Reset at every response boundary.** A single `_run_once` `stream_events()` spans
  multiple model generations (tool call → result → generate again). The detector
  **resets on each `ToolStart` / message boundary** so it only ever sees *one*
  response — this is what keeps us in "case 1" and out of cross-step "case 2".
- **CJK-aware.** Normalization and "truncate to a complete sentence boundary" must
  recognise `。！？；` as well as latin `.!?` — model output is Chinese with no spaces.
- **Detect on normalized text, truncate on raw text.** Normalization (collapse
  whitespace runs, casefold) is for *judging* only; the actual truncation must land on
  a raw-buffer offset, so keep a normalized→raw offset mapping (or restrict
  normalization to reversible whitespace-collapsing).
- **L3 live behaviour = keep the repeats (decision "b").** We do **not** retract the
  already-streamed deltas. The user sees the repeated text + a notice — deliberate
  transparency that *this LLM has a problem*. Truncation is applied **only** to the
  persisted `Message` and the `history` fed to the next turn (so the loop is not fed
  back in and re-triggered).
- **Persisted notice.** Stamp the `Message` with `stopped_reason="repetition"` so a
  page reload (which shows the truncated clean text) still renders the notice — the
  user never mistakes a truncated answer for a normal one.
- **Finish, don't error.** End the turn with `RunDone` (not `RunError`); the user
  keeps the clean partial output.
- **False-positive guards.** Pause detection **inside fenced code blocks** (``` ```);
  rely on `R=3` exact-block repetition to spare markdown tables (rows are similar but
  rarely byte-identical) and enumerated lists.
- **Non-streaming path.** `_run_once_nonstream` (`WORKSPACE_AGENT_STREAM=0`) runs the
  same detector **once** over the final text. `DecideThenActModel`'s final answer is
  streamed, so it rides the main path.
- **Off switch.** A global enable flag (env, `WORKSPACE_*` style) plus per-`AgentConfig`
  preset overrides for `R` / `L_max` / `W` / channels.

---

## Phase 1 — `agent/repetition.py` detector + unit tests

**Goal.** A pure, dependency-free detector that, fed an incremental text stream, flags
a degenerate tail-period repetition and reports the **raw truncation offset** (where
the clean text ends, before the loop began).

**Changes.**
- New `agent/repetition.py`: a `RepetitionDetector` class with `feed(delta) -> bool`
  (or returns a small result carrying the truncation offset), `reset()`, and a
  `truncation_point()` accessor. Tunables `R`, `L_max`, `W` via constructor.
- Tail-period search over the normalized buffer; CJK + latin sentence-boundary helper;
  fenced-code-block pause; normalized→raw offset mapping.

**DoD / tests (TDD).**
- Triggers: `"the the the…"` (small `p`); a repeated multi-sentence block
  (`"讓我檢查foo,現在有遇到問題xxx,"` ×3, large `p`); reasoning-style loop text.
- No false positives: numbered/bulleted lists, a markdown table, a long normal answer.
- Code fence: repetition inside ``` does not trigger.
- Truncation point lands on the clean sentence boundary **before** the loop (CJK + latin).
- `reset()` clears state between responses.

---

## Phase 2 — Wire detector into `produce()` + L3 graceful block + SSE event

**Goal.** A live streaming turn that loops is stopped, the repeats stay visible, the
turn ends with `RunDone`, and persisted/history text is truncated.

**Changes.**
- New SSE event `RepetitionStopped` in `api/events.py`, mirrored in `web/src/events.ts`
  (per the keep-in-sync convention); carries the channel and a human-facing reason.
- In `produce()` (`litellm_runner.py`): feed each content/reasoning delta to the
  per-channel detector; on trigger → `streamed.cancel()`, stop the `async for`, emit
  `RepetitionStopped`, run the normal finalize (flush splitter, `AgentMetrics(final)`),
  then `RunDone`. Reset detectors on `ToolStart` / message boundary.
- Truncate `content_buf` (→ persisted `Message`) and the history representation to the
  detector's truncation point; stamp `stopped_reason="repetition"` on the `Message`
  model (`Message` / `KbMessage`).
- `_run_once_nonstream`: one-shot detector check over the final text → same truncation
  + event.
- Pure-reasoning loop (no content) → dedicated finalize copy (see Phase 4).

**DoD / tests (TDD).**
- `ScriptedAgentRunner` fed a degenerate delta stream emits `RepetitionStopped` then
  `RunDone` (**not** `RunError`); persisted `Message` is the truncated clean text and
  carries `stopped_reason`; the live event sequence still contains the pre-truncation
  deltas (decision "b").
- Detector resets across a tool-call boundary (a legit repeat *across* steps does not
  trigger).
- Token-accounting after `cancel()` falls back to approximate `final` metrics without
  aborting the finalize.

---

## Phase 3 — L1 sampling prevention params

**Goal.** An operator can set sampling penalties per `AgentConfig` preset; they reach
the model on backends that honour them.

**Changes.**
- Add optional `frequency_penalty` / `presence_penalty` / `repetition_penalty` to the
  `AgentConfig` preset schema (default `None` = inherit, mirroring the `base_url` /
  `api_key` "empty = inherit" convention).
- `_agent_for()` (`litellm_runner.py:286`): map `frequency_penalty` /
  `presence_penalty` to native `ModelSettings` fields; `repetition_penalty` into
  `extra_body`. Code comment documenting the **Ollama Go-runner silent-drop** caveat
  and why L2/L3 remain the real guard.

**DoD / tests (TDD).**
- A preset with `frequency_penalty` set produces a `ModelSettings` carrying it; the
  `repetition_penalty` path lands in `extra_body`.
- Unset penalties leave `ModelSettings` at the model default (no spurious params).

---

## Phase 4 — FE notice + copy

**Goal.** The user sees a clear, internals-free notice when a turn is stopped for
repetition, both live and on reload.

**Changes.**
- Render `RepetitionStopped` (and the persisted `stopped_reason`) in the shared
  `web/src/components/AgentEntryView.tsx` as a one-line notice below the message.
- Copy describes the action/outcome, no system nouns (per the UI-copy convention):
  e.g. "偵測到模型一直重複,已為你收尾。" — and a distinct line for the
  pure-reasoning-loop case ("模型在思考時陷入重複,已中止。").

**DoD / tests (TDD, vitest).**
- A message with `stopped_reason="repetition"` renders the notice; a normal message
  does not. Live `RepetitionStopped` event renders the notice during streaming.

---

## Open for v2 (explicitly deferred)

- Cross-step / cross-turn repetition (case 2).
- Mid-stream **retry** with bumped sampling params (needs the `progress_made` gate
  reworked, #26).
- **DRY** sampler / `no_repeat_ngram_size` (backend support gaps).
- Spectral / near-repeat detection (SpecRA) for paraphrased loops that exact-period
  detection misses.
