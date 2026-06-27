# Plan — #284 `make_deck`: designed-pptx via a multimodal sub-agent loop

> Out-of-the-box PPT **craft**, not just the lib. The agent gets a `make_deck`
> tool that builds a *designed* `.pptx` (pptxgenjs + absolute-coordinate layout
> + design tokens + a render→see→fix loop) by driving a **multimodal sub-agent**
> internally — so even a weak main agent calls one tool and gets a polished deck.

Grilled (`/grill-me`) and locked. Faithful port of the proven `designed-pptx`
Claude skill (pptxgenjs, Node) into ai-workspace's seams.

## Architecture — three layers, one trust boundary

```
main agent ──calls──▶ make_deck  (FunctionTool, API side / TRUSTED)
                        │  ① call multimodal model (IVlm, external dep) → pptxgenjs code
                        │  ② write build.js / theme as DATA into the workspace
                        │  ③ exec ─────────────▶ sandbox (dedicated deck image)
                        │       node build.js → deck.pptx
                        │       soffice → pdf → pdftoppm → per-slide PNG
                        │  ④ read PNG DATA → feed to multimodal model → critique
                        │  ⑤ ≤N rounds, stop on PASS / budget, never empty-handed
                        ▼
                     deck.pptx path + summary   (live progress relayed throughout)
```

* **API side** (trusted): only LLM calls + data file IO + issuing `exec` + loop
  control + progress relay. Runs **zero** model-generated code. Holds the model
  credentials. Mirrors `ask_knowledge_base` (sub-agent reasoning is API-side).
* **Sandbox side** (dedicated deck image): node + pptxgenjs + libreoffice-impress
  + poppler-utils + fonts-noto-cjk. **All** code execution happens here via `exec`.
  Never given the model credentials.

## Locked decisions

1. **Form** — a dedicated tool, NOT a python-stack helper, NOT a JSON-spec generator.
2. **Nature** — agentic: an internal multimodal sub-agent owns the craft; the main
   agent passes only high-level intent.
3. **Loop (ii)** — one capable multimodal model both *sees* rendered slides and
   *writes* the pptxgenjs fix, iterating. Reuses the existing **`IVlm`** streaming
   multimodal interface (`stream(prompt, images=…)`); no new LLM-layer primitive.
4. **Execution (甲)** — node / python / soffice / pdftoppm run in the sandbox via
   `exec`; LLM calls + loop control stay in the trusted API; credentials never
   enter the sandbox.
5. **Input contract** — `goal`, `audience?`, `source?` (workspace files the loop
   reads as material — text inlined, images shown to the model), `notes?`,
   `style?`, `length?`, `out_path?` (default `./deck.pptx`).
6. **Model** — usage-reference `agents.designed_pptx` → preset → `resolve_llm_chain`
   → `FallbackVlm` (external dependency + fallback). Unset ⇒ fall back to
   `kb.vlm_llm`. Requires a **multimodal** model; absent ⇒ fail-loud (like
   `read_image`). Never assumes hosted — see `feedback_ai_external_dependency`.
7. **Budget** — ≤ N rounds (default 4), stop on PASS / budget, **always** return
   the last `.pptx`; all LLM calls stream + relay progress (`feedback_always_stream_llm`).
8. **Fonts** — theme CJK token defaults to `Noto Sans CJK TC` (render-fidelity:
   what soffice renders == what the user exports == what the model sees).
9. **Runtime packaging** — a dedicated **sandbox image** `docker/Dockerfile.workspace-deck`;
   the API image never gets node.
10. **Mounting** — `rca` + `playground` `agent.tools`. Shipped as a committed asset
    (under the 100% gate), not in-house/gitignored.
11. **v1 creates only** (no edit-existing-deck).

## Implementation choices (flagged, approved)

* **Bespoke bounded loop**, not the full AgentRunner — fixed generate→render→see→fix
  cadence, explicit budget, fully unit-testable to 100% with a fake `IVlm` + injected
  async exec/read/write seams (no real sandbox/model in unit tests).
* **Two-piece** — host-side `make_deck` FunctionTool + the dedicated sandbox image.

## Phases (flat, TDD)

* **P1** — `docker/Dockerfile.workspace-deck` (node + pptxgenjs + libreoffice-impress
  + poppler-utils + fonts-noto-cjk) + in-sandbox `render` helper (pptx → per-slide
  PNG). Integration test renders a CJK sample deck via LocalProcessSandbox on host.
* **P2** — `agents.designed_pptx` config ref (schema + loader + validation) +
  `get_designed_pptx_vlm` factory (mirror `get_kb_vlm`; fallback to `kb.vlm_llm`).
* **P3** — the bespoke bounded deck loop (`agent/deck/`): dependency-injected,
  generate→render→see→fix, budget / PASS / never-empty / progress relay. Unit tests
  with a fake `IVlm` + fake exec/io.
* **P4** — `make_deck` FunctionTool (schema, `AgentToolContext.deck_vlm`, register,
  expose-gate) + wire `deck_vlm` in the API context + add to `rca` / `playground` app.json.
* **P5** — craft port: sub-agent system prompt distilled from the `designed-pptx`
  references + theme/starter assets as package data; render helper → local `soffice`;
  `config.example.yaml` `designed-pptx` preset docs.
* **P6** — full suite + 100 % gate + `ruff` + `ty`; live canned check (real
  `make_deck` → real `.pptx` → eyeball); commit → PR → CI green + no conflict → merge.
```
