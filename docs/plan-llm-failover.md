# Plan — LLM Failover & Busy-Aware Switching (#196 + #131)

> Status: grilled & locked (see `/grill-me` session). Implement with `/tdd`.
> Flat integer phases (P1, P2, …) per CLAUDE.md.

## Problem

Our deployment has **several models available**, each of which can get **打到太忙
(overloaded)**. The dominant failure mode is *busy*, not *down*, and busy shows up
**both ways**: sometimes a fast error (500/503/429), sometimes the request just
**queues / hangs** with no error until a timeout. We need to **dynamically switch to
the next model** when the current one is too busy.

- **#131** — `LitellmVlm.stream()` (`kb/vlm/litellm.py:31`) has *no* retry; a transient
  server 500 fails the whole ingest.
- **#196** — want a **priority list** of models; when the front one fails, auto-switch
  to the next.

These are **one feature**: retry and fallback are the *same loop* — "retry" = advance
to the next entry in the priority list.

## Core decisions (locked)

1. **One mechanism, not two.** #131 + #196 = a single **strict-priority failover**
   loop. Same-model retry ≈ 0 (a busy model stays busy — *switching* is the retry).

2. **Hand-rolled shared core, not litellm Router.** A small in-house **failover policy
   core** sits behind our existing interfaces. litellm `Router` / completion-level
   `fallbacks=` were **rejected as the primary mechanism** because:
   - We need **TTFT-only** busy detection (fail over if no *first token* within N s, but
     never kill a model that has started streaming). litellm's `timeout` is a *total*
     timeout and cannot express this.
   - Unit-testability under the **100 % coverage gate** — a hand-rolled core composes
     injectable fake providers + an injected clock; Router would force mocking litellm
     internals or integration-only tests.
   - The agent path needs a custom SDK `Model` **anyway** (no clean litellm-native agent
     fallback), so Router would mean *two* mechanisms.
   - We still keep litellm's per-call `timeout` and its typed exceptions for
     classification; we just don't delegate the *loop* to it.

3. **KB and app share one brain ("一脈相承").** One generic policy core, three thin
   interface adapters — *not* two divorced implementations:
   - `FallbackLlm` (implements `ILlm`) — KB retrieval LLM, VLM formatter, etc.
   - `FallbackVlm` (implements `IVlm`) — VLM describe / read_image.
   - `FallbackModel` (implements the OpenAI Agents SDK `Model`, wraps N `LitellmModel`)
     — agent / sub-agent chat (RCA + KB chat + wiki), **same core**.
   - Embedder reuses the core's classification + cooldown (non-streaming flavour).

4. **Failover triggers.**
   - **Fast errors → switch immediately, 0 same-model retry:** `APIConnectionError`,
     `NotFoundError` (404), `AuthenticationError` (401), `BadRequestError` (400),
     `InternalServerError` (500/503), `RateLimitError` (429), `ServiceUnavailableError`,
     `Timeout`.
   - **TTFT timeout (streaming) → switch:** no first token within `ttft_timeout_s`.
   - On any switch trigger → **cooldown** the `(model, endpoint)` + advance to next entry.

5. **Strict priority order, NOT load-balancing.** The models have a **quality order**;
   always prefer the best, degrade only under pressure. (Load-balancing / least-busy was
   rejected — it would spread to worse models even when the best is fine.)

6. **Cooldown = process-global, shared across roles.** One registry keyed by
   `(model, endpoint)`. Because KB and app hit the **same** models, "app found X is hot"
   should make **KB skip X too**. Priority *lists* stay per-role; *"who is busy"* is shared.
   Recovery is **time-expiry half-open**: after `cooldown_s`, the next request naturally
   re-probes; success un-freezes, failure re-freezes. No background prober.

7. **Built-in retries off in wrapped roles.** Inner litellm calls run `num_retries=0`;
   SDK `ModelRetrySettings` off. The core owns the whole policy — otherwise litellm's
   blind retry wastes `n × m` seconds on an already-busy primary before we get to switch
   (the key objection that drove this design).

8. **Mid-stream is unrecoverable.** Once the first token is emitted, a later failure
   **raises** (a stream the user already saw can't be restarted). A mid-stream **stall**
   (no further tokens) longer than `idle_timeout_s` also **raises** — it does *not*
   fail over. (Mirrors `SandboxSettings.exec_timeout`/`log_timeout` total-vs-idle split.)

9. **Scope = all roles.** KB retrieval LLM, VLM, VLM formatter, `DecideThenActModel`,
   agent/sub-agent chat (RCA + KB chat + wiki), embedder.

10. **Embedder is special: replica-only.** Its fallback chain may only contain the
    **same embedding model on different endpoints** — switching to a *different* embedding
    model corrupts the vector space (and breaks `KB_EMBED_DIM`). Config validation is
    **fail-loud** if an embedder fallback names a different model/dim.

11. **Config lives in the preset, usage unchanged.** The `fallbacks` chain is defined
    **inside the `Preset`** (`schema.py:351`); any role that references the preset
    inherits the chain — reference sites don't change. Rules:
    - `fallbacks: list[str]` = **names of other presets**, in priority order.
    - **No recursion** — a fallback preset's *own* `fallbacks` are ignored; the chain is
      literally `[preset, *preset.fallbacks]`.
    - **Roles don't override the chain** — different needs = a different preset. (Role-side
      inline `model`/`llm`/`reasoning_effort` overrides still tweak the *primary* only.)
    - **`ttft_timeout_s` / `cooldown_s` / `idle_timeout_s` are per-preset, overriding a
      global `failover:` default** — TTFT is model-dependent (a local qwen should switch
      after ~8 s of silence; claude's first token is legitimately slower).

12. **Observability.**
    - *Operators (always):* structured log on every switch/cooldown; the existing global
      litellm `CustomLogger` already records each attempt in the replayable call log, so
      "tried A → failed → tried B" is free there. Cooldown state is introspectable.
    - *Interactive chat (agent + KB chat):* when a turn runs on a **non-primary** model,
      emit a de-jargoned signal into the stream (reuse the `agent_log`/`ToolLog` channel
      that `ask_knowledge_base` uses) — the user is reading a **degraded** answer and
      should know. UI copy: action/outcome only, no model ids.
    - *Ingestion (VLM / embedder / retrieval):* log-only (no live viewer).
    - *Deferred:* a Diagnostics "model health / currently-cooling-down" view — **not v1**.

## Config shape (target)

```yaml
failover:                     # global defaults
  ttft_timeout_s: 8           # streaming: no first token within this ⇒ switch
  cooldown_s: 30              # how long a (model,endpoint) stays skipped after a switch
  idle_timeout_s: 120         # mid-stream stall ceiling ⇒ raise (NOT switch)

agents:
  presets:
    kb-default:
      model: "ollama_chat/qwen3:14b"     # primary (highest priority)
      fallbacks: [qwen3-8b-local, claude-opus]   # other preset names, in order
    claude-opus:
      model: "..."
      ttft_timeout_s: 30                 # slow model relaxes the global default

kb:
  retrieval_llm: { preset: kb-default }  # usage unchanged — inherits the chain

  embedder:                              # NOT a preset → replica-only fallback
    model: "ollama/bge-m3"
    fallbacks:                           # same model, different endpoints ONLY
      - { base_url: "http://embed-2:11434" }
```

Effective chain for a role = `[preset.model_spec, *(resolve(name) for name in preset.fallbacks)]`.

## Failure classification (single source of truth, in the core)

| Class | litellm exceptions | Action |
|---|---|---|
| **busy / transient** | `InternalServerError`, `ServiceUnavailableError`, `RateLimitError`, `Timeout`, `APIConnectionError` | cooldown + switch |
| **config / hard** | `NotFoundError`, `AuthenticationError`, `BadRequestError` | cooldown + switch (still try next; a *different* model may accept it) |
| **TTFT timeout** (streaming only) | — (our timer) | cooldown + switch |
| **mid-stream error / stall** (after first token) | any / `idle_timeout_s` | **raise** (no switch) |

## Phases (flat)

- **P1 — Failover policy core.** Cooldown registry (`(model,endpoint)` → frozen-until,
  injected clock), error classification, strict-priority advance, TTFT race + idle ceiling,
  and a `on_switch(from, to, reason)` callback hook for observability. Pure; unit-tested
  with fake providers (program "fast error / stall past TTFT / succeed on k-th") + fake clock.
- **P2 — Config & resolution.** `Preset.fallbacks` + per-preset `ttft_timeout_s` /
  `cooldown_s` / `idle_timeout_s`; global `FailoverSettings`; loader/merge; a factory helper
  that resolves a preset name → ordered list of provider specs (no recursion). Unit-tested,
  incl. the fail-loud embedder same-model guard.
- **P3 — Streaming `ILlm`/`IVlm` adapters.** `FallbackLlm` + `FallbackVlm` over the core;
  wire into `get_kb_llm`, `get_kb_vlm`, `get_kb_vlm_formatter`. **Delivers #131** (VLM) plus
  KB retrieval + formatter. Inner `LitellmLlm`/`LitellmVlm` set `num_retries=0`.
- **P4 — `DecideThenActModel`.** Autonomous-RCA `acompletion` path through the core
  (total-timeout flavour, no TTFT since non-streaming structured step).
- **P5 — Embedder.** Replica-only fallback (`LitellmEmbedder`); reuse core classification +
  cooldown; total-timeout. Drop the now-redundant blind `num_retries` in favour of the chain.
- **P6 — Agent path.** `FallbackModel` (SDK `Model`) wrapping N `LitellmModel`; failover at
  **per-model-call** granularity (mid-run busy ⇒ later calls use the spare). `stream_response`
  = TTFT on first event; `get_response` = total timeout. Wire into `LitellmAgentRunner`
  (shared by RCA + KB chat + wiki). SDK retry off.
- **P7 — Observability surfacing.** Operator log lines on `on_switch`; confirm `CustomLogger`
  captures attempts; interactive-chat degradation note via `agent_log`/`ToolLog` on the agent
  + KB chat surfaces (de-jargoned, i18n). Diagnostics health view explicitly out.
- **P8 — Live check + docs.** A live canned check that *actually* exercises failover (point
  the primary at a dead/forced-busy endpoint, assert it lands on a working spare) per the
  "LLM features need live checks" DoD; update `config.example.yaml` + a docs section. Full
  suite + 100 % gate + ruff/ty green.

## Testing strategy

- Injectable **fake providers** with programmable failure modes (fast error, stall-past-TTFT,
  succeed-on-k-th) and an **injected clock** so TTFT + cooldown are unit-tested without real
  `sleep`. Matches the codebase's `HashEmbedder` / `MockSandbox` / `ScriptedAgentRunner` style.
- 100 % coverage gate; targeted tests during iteration, full suite + gate once at the end.
- P8 live check is part of Definition-of-Done (fake-LLM tests ≠ feature works).

## Rejected alternatives

- **litellm Router / completion `fallbacks=`** as the loop owner — can't do TTFT-only
  detection; harder to unit-test under the 100 % gate; agent path needs a custom `Model`
  regardless ⇒ would be two mechanisms.
- **Nested "built-in `num_retries` inside, our fallback outside"** — litellm's retry is blind
  to the fallback chain and burns `n × m` s on a dead/busy primary before we can switch.
- **Load-balancing / least-busy routing** — models have a quality order; we prefer the best
  and degrade only under pressure, not spread.
- **Per-role `fallbacks` lists** — defined in the preset instead (DRY, usage unchanged).
- **Recursive fallback chains / role-level chain overrides** — rejected for predictability.
