# Contributions

A curated log of work on **ai-workspace** — a pluggable KB + multi-app agent platform
(FastAPI + OpenAI Agents SDK + LiteLLM/Ollama, React/Vite frontend, specstar persistence).

Each entry links an issue to its PR and current status. Grouped by area, newest-leaning within
each group. Status legend:

- ✅ **Merged** to `master`
- 🟢 **Shipped** on a branch / demo (not via a `master` PR)
- 🚧 **In flight** — built and/or under review, not yet merged
- 📋 **Planned** — design locked via `/grill-me`, not yet built
- ⏸️ **Paused / suspended**

> Statuses reflect the project working-memory snapshot and may lag the live repo for the most
> recent items. PR numbers are GitHub PRs unless noted.

---

## Platform & app framework

The platform turns the original RCA tool into one of several Apps (RCA, Diagnostics, Topic Hub),
all template-driven from in-code app directories.

| Issue | What | PR | Status |
|---|---|---|---|
| #89 | RCA → App templates: multi-app platform (per-App resource, `WorkItemBase`, 3-layer agent resolve, launcher) | — | 🟢 foundation built (later App work builds on it) |
| #94–97 | Post-#89 cleanup: delete `rca/` pkg (fail-loud), `tool_packages`→`tooling/`, de-investigation routes → `/a/{slug}/items/{id}/`, regen autocrud | — | 🟢 |
| #43 | Collaborative workspace: multi-user investigation collab (shared files + chat, author-stamped, broadcast `/stream`, mentions) | — | ✅ |
| #200 | Multichat escape hatch: every App workspace multichat-capable & template-driven (killed the slug fork); unified on `ItemChatShell` | #233 | ✅ |
| #231 | Diagnostics App: sanity-coverage matrix + AI judge per cell + per-model verdict + custom questions | #277 | ✅ |
| Topic Hub | New cross-collection inquiry App: file-based memory + `collections.json` + multi-chat + N workflows/profile + glossary tools | — | 🟢 manual + plan locked, built incrementally |
| #298 | Author-skill: user+AI co-create loadable skills (per-workspace `.skill/`, `save_skill` tool, `SHARED_SKILLS` registry, FE panel) | #301 | ✅ |

## Knowledge Base — ingestion, retrieval, scoring

| Issue | What | PR | Status |
|---|---|---|---|
| #39 | KB parsers plan: parsers emit whole-file Documents, splitter owns granularity (P1–P6 landed) | — | 🟢 partial (JSON/CSV/VLM/FE remaining) |
| #50 | LLM wiki: 2nd parallel pipeline alongside chunk-RAG (re-query agent loop), cross-worker concurrency via specstar CAS | — | 🟢 P1–P8 |
| #90 | Per-collection wiki guidance: append (not replace) onto bundled wiki prompts; 2 non-indexed Collection fields | — | 🟢 (demo) |
| #88 | Chunk-based token count: collection "≈ tokens" from extracted text (CJK-aware) not blob bytes/4; indexed + Schema v4 backfill | #293 | ✅ |
| #105 | Doc scoring: per-collection `quality_rubric` → AI scores each doc (windowed map-reduce) → badge/rationale + search down-weight | #299 | ✅ |
| #103 | Chunk-count aggregate: count via GROUP BY push-down, not materialising rows (fixed slow documents list) | #297 | ✅ |
| #263 | Provenance precise query: page/sheet structural chunk filter layered on vector search (`document`/`page_from`/`page_to`/`sheet`) | #276 | ✅ |
| #254 | PDF provenance: page/section carried into chunks for global context (all-parser provenance dict) | — | 📋 |
| #195 | `kb_search` per-turn cap: bound searches per KB-chat turn (config `kb.max_searches_per_turn`), graceful sentinel + budget footer | #213 | ✅ |
| #115/#116 | Table-aware chunking: VLM-markdown truncation fix + row-explode large tables (offset-merge rebuilds), `table_max_rows` tunable | — | 🟢 (#115 committed, #116 TDD) |
| #106 | Context cards: deterministic glossary (`ContextCard` Struct, many-to-many keys, exact `norm_keys` lookup) alongside `kb_search` | — | 📋 |
| #111 | Card update: let Topic Hub UPDATE context cards (read-before-write CAS, `upsert_context_card` workflow) | — | 🚧 built, not committed |
| #133 | →collections glossary: AI-drafted defs in classify, deterministic assembly, ⚠️ markers, approve/reject/revise round loop | #147 | ✅ |
| #205 | Card-diff review: Topic Hub →collections shows before/after so overwrites aren't blind-signed (Monaco DiffEditor) | #229 | ✅ |
| #184 | Doc-list flicker fix: stable paging (`created_time` desc + `resource_id` asc) so "Indexing N…" stops bouncing | #193 | ✅ |
| #185 | SVG→PNG blur fix: viewBox parse + cairosvg long-side scale to 2048 | #228 | ✅ |
| #101 | Collection download + import: ZIP + manifest round-trip, zip-slip guard, verbatim members | — | 🚧 building |
| #247 | Download files/folders: shared FileTree "Download" (file → `<a download>`, folder → raw ZIP), reuses #101 | #259 | ✅ |

## Agents & tools

| Issue | What | PR | Status |
|---|---|---|---|
| #270 | KB tools merge: collapse `ask_knowledge_base` sub-agent; `{kb_search, search_wiki, lookup_glossary}` as composable primitives + shared KB guidance | — | 📋 grilling impl |
| #275 | `lookup_user` tool: handle → record (`find_by_handle`); RCA default tools must be in `app.json`, not just the fallback list | #295 | ✅ |
| #112 | `read_image` tool: VLM-over-workspace-image agent tool (RCA/playground/Topic Hub), shared describer | — | 🚧 built, not committed |
| #284 | `make_deck`: intent → multimodal sub-agent loop (pptxgenjs + render→see→fix via VLM), toolchain in sandbox-host image | #289, #313 | ✅ |
| #285 | `sci-plot`: extensible scientific-plotting catalog (`IChart` registry) + inline image display + VLM closed-loop self-correction | #291 | ✅ |
| #252 | Office tools: ppt/excel libs into the python-stack venv carrier; `DockerSandbox` deprecated → sandbox-host | #269 | ✅ |
| #241 | Workspace awareness: shared `apps/_base.md` defensive preamble (function-tools-not-shell, orient-first, refuse host reach); `ls`→`list_files` | #253 | ✅ |
| #221 | Clickable `[n]` citations: shared renderers make inline citations clickable in chat (fixed latent ReportRenderer pill bug) | #272 | ✅ |

## Workflows

| Issue | What | PR | Status |
|---|---|---|---|
| #100 | Workflows: API-triggered headless orchestration, FS-as-journal, Python `run()` (not DSL), produce→review→commit with `human_gate` | — | 🟢 foundation built |
| #288 | Workflow steering: conversational free-text → LLM plan → confirm blast-radius → deterministic apply → resume same run incrementally | #296 | ✅ |
| #287 | Workflow authoring DX: `python -m workspace_app.workflow {new,check}` CLI (scaffold recipes + static drift check) + authoring docs | #292 | ✅ |
| #178 | Per-step workflow status: real liveness (sandbox stdout → step event) + persisted per-step board, no heartbeat/auto-kill | #215 | 🚧 open, awaiting CI |
| #176 | Gate step highlight: human_gate emits StepStarted/StepPassed so the reviewed phase turns green | #220 | ✅ |
| #136 | Journal folder: move `step_*` journal out of item root into `/.workflow/<id>/` | #155 | ✅ |
| #197 | Workflow trigger upload: run trigger accepts multipart file uploads (filename=path, zip-slip guard) | #237 | ✅ |
| #283 | Workflow operator UX: pre-flight checklist dialog + interactive Timeline + run as first-class "don't close the door" | — | 📋 plan locked (spun off #287/#288) |
| — | Workflow observability: render dropped workflow events in feed + no-op message/banner + app-scoped export fix | — | 🟢 (branch) |

## Reliability, infrastructure & ops

| Issue | What | PR | Status |
|---|---|---|---|
| #312 | Job runner ⊥ API: split job runner into worker pods + per-job k8s HPA; API as pure producer; all 4 JobTypes split | #314 | ✅ |
| #60 | HTTP Sandbox: 4th Sandbox client + self-hosted FastAPI host, uid+cgroup isolation, stateless pod-addr routing, NDJSON stream | #238 | ✅ |
| #251 | Sandbox-host standalone split: hard-split host into `sandbox-host/` (own pyproject/lock, wire-contract only) | — | 🚧 built, authorized |
| #196 + #131 | LLM failover: priority-list model fallback + VLM retry (hand-rolled TTFT-aware core, process-global cooldown) | #214 | ✅ |
| #248 + #249 | Index retry + progress: 3-layer transient retry + monotonic CAS `units_done/units_total` progress bar | #266, #267 | ✅ |
| #227 | Index fan-out: fix RabbitMQ 406 consumer-ack timeout by fanning big jobs into per-unit jobs + CAS join | #235 | ✅ |
| #204 | PG connections: "too many clients" fixed via specstar v0.11.9 shared connection pool per DSN | #216 | ✅ |
| #208 | Boot observability: `boot_step` narration + `pg_connect_timeout` (pod no longer hangs silently on unreachable PG) | #211 | ✅ |
| #186 | Job updater: derived artifacts credit requester; strip job-lifecycle `created_by` shadowing (`preserve_job_creator`) | #258 | ✅ |
| #219 | Workspace filestore → Binary + streaming upload (never OOM); per-file `WorkspaceFile`; 2GB cap → 413; migration boot step | #257 | ✅ |
| #245 | Workspace quota (20GiB) + blob GC: 507 mid-stream gate + specstar v0.11.10 ref-count GC + FE usage bar | #278 | ✅ |
| #177 | API `/api` namespace: global prefix fixes FE route ⨯ backend route collisions; route-aware test clients | #194 | ✅ |
| #199 | Interrupt → system-first crash fix: fold "[Response interrupted]" into preceding assistant (replay-only, no migration) | #210 | ✅ |
| #113 | Repetition guard: detect LLM repetition-loop degeneration + graceful mid-stream block (3-layer) | — | 🟢 detector built |
| #146 | Repetition detector too aggressive: content-agnostic loosening (no table special-casing) so wide tables survive | #154 | ✅ |
| #202 | App chat stuck "still preparing" (multipod): FE store-poll safety net + nginx sticky by item_id | — | 📋 grill-locked (pub/sub deferred) |
| #201 | Model select not sticking: strip server-generated revision metadata before read-modify-write PUT | #209 | ✅ |
| — | Job-pod split + worker acting-user: index/wiki consumers run in dedicated pods, preserve real updater via `rm.using(user=…)` | — | 🟢 |
| — | Observability: startup resolved-config provenance dump + replayable LLM call log (global litellm CustomLogger) | — | 🟢 (demo) |
| — | Config → Hydra migration | — | ⏸️ suspended (too conflict-heavy; runs hand-rolled loader) |

## Frontend & UX

UX hardening ran as two epics — #157 (round 1) and #169 (round 2) — plus standalone polish.

| Issue | What | PR | Status |
|---|---|---|---|
| #158 | Global nav + breadcrumb: top bar via layout route, app switcher, fix backtrack-to-`/` | #165 | ✅ |
| #159 | Chat primary, IDE behind toggle: `manifest.Layout.primary_surface` + per-App `ideCollapsed` toggle | #167 | ✅ |
| #160 | De-jargon UI + simple i18n: hand-rolled typed i18n (zh-TW + en), incremental de-jargon | #166 | ✅ |
| #161 | Onboarding + empty states: versioned welcome modal (platform + per-App) + empty-state copy | #168 | ✅ |
| #162 | KB IA + index status: per-tab subtitles + collection-page index-status strip | #164 | ✅ |
| #170 | Async status/progress feedback: KB index status strip rework + workflow gate card pinned + skeletons + tool-card running banner | #188 | ✅ |
| #171 | De-jargon term sweep: terminology table (sandbox→執行環境, indexing→處理中, retrieval modes relabeled) | #187 | ✅ |
| #172 | Core-action discoverability: upload buttons + drag-drop overlay, re-index on Documents tab, nav switcher labels, scope button | #189 | ✅ |
| #173 | Inline concept help: expandable "what's here" strip, glossary framing, Wiki "AI-written, editable" badge | #174 | ✅ |
| #118 | Sanity output modal: truncated Diagnostics output → click opens read-only full-text modal (+ grade/latency footer) | #153 | ✅ |
| #132 | Multi-chat UX: Topic Hub chat list redesign (switcher dropdown + manage modal, status badges, recency sort) | — | 🚧 building |
| #142 | Collections picker: Topic Hub UI to select a hub's collection set (vs hand-editing `collections.json`) | — | 🚧 building |
| #271 | KB chat collection picker: top-6 ranked pills + shared `CollectionsChecklist` modal | #279 | ✅ |
| #280 | Collection tiers: per-profile defaults + web override + `ask_knowledge_base(rank)` tiered fallback | #302 | ✅ |
| #226 | System font size: rem scaling (not zoom), platform-global settings (Font/Theme/Lang/About) | #239 | ✅ |
| #180 | Context-cards left scrollbar fix (max-height + overflow) | #191 | ✅ |
| #151 | HTTP loading: global progress bar (`useIsFetching`/`useIsMutating`) + Skeleton + false-empty gating | — | 🚧 built, uncommitted |
| #93 | KB URL coverage: URL-ify KB views (stay on react-router-dom, not TanStack) | — | 📋 plan locked |
| — | Turn-wait UX: 4-state "working…" indicator (準備/等候模型/思考中/回覆中) | — | 🚧 built, uncommitted |

## Quality, CI & tooling

| Issue | What | PR | Status |
|---|---|---|---|
| #127 | First GitHub Actions CI + 3 README badges (CI status + backend/frontend Codecov); coverage parallel+combine for xdist | #127 | ✅ |
| — | Reasoning-control health check: per-model "can reasoning be turned off?" probe + FE display | — | ⏸️ paused mid-design |

---

## Working principles

Recurring conventions that shaped the work above:

- **Plan first, then test-first.** New features / bug reports start with `/grill-me`; implementation
  drives through `/tdd` (red-green-refactor). Plans use flat integer phases (Phase 1, 2, …) — never `1a`/`1b`.
- **Local-first AI.** Default to LiteLLM + a small local Qwen via Ollama; hosted AI is a pluggable
  external dependency, never a requirement. Every LLM call streams.
- **LLM features need live checks.** Fake-LLM tests prove plumbing, not behavior — a live canned
  check is part of Definition of Done.
- **Research-backed decisions are cited** in code comments *and* the PR body.
- **Lean on the framework.** Prefer specstar built-ins (indexed queries, migrate route, CAS,
  ref-count GC) over hand-rolled ops; index fields to filter/sort instead of fetch-all + Python-filter.
- **Toolchain:** `uv` + `pytest` + `ruff` + `ty` + `coverage.py` directly; full suite + 100%
  coverage gate before merge; whole-project `ty` (CI checks `tests/` too).
- **UX copy describes actions/outcomes**, never internals (mimes, system nouns, model ids).
