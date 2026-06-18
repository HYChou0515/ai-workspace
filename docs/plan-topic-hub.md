# Plan — Topic Hub

> The build plan for **`docs/topic-hub.md`** (the manual / acceptance criterion).
> **Flat integer phases** (`Phase 1`, `Phase 2`, …; never `1a`/`1b`) — foundations
> first, each phase a shippable, testable increment driven by **`/tdd`**
> (red→green→refactor). Backend: pytest + 100% coverage on new modules; FE: vitest
> (FE follows `/tdd` too). Iterate with the changed-behaviour tests + ruff + ty; run
> the full suite + coverage gate once at the end of each phase.
>
> Section refs (`§N`) point at the manual. `WF §N` points at `docs/workflows.md`.

The work splits into **platform bricks** (§2 of the manual — general, any App can
use) built **foundations-first**, then **the Topic Hub App** that composes them, then
its **workflows**, then **FE**.

---

## Phase 1 — Hyphenated-slug App loader + `apps/topic-hub/` skeleton

**Goal.** A hyphenated App slug (`topic-hub`) loads and appears on the launcher.

**Changes.**
- Load an App's `model.py` **by file path** (mirroring `workflow/discovery.py`'s
  file-path exec of `run.py`) instead of `import_module("apps.<slug>.model")`, so a
  dir name with a hyphen works (manual §2 note). Touch `apps/registry.py` /
  `apps/catalog.py` discovery.
- Create a minimal `apps/topic-hub/` (`app.json` with `slug: "topic-hub"`,
  `model.py` with a trivial `WorkItemBase` subclass + `INDEXED_FIELDS`,
  `prompts/system.md`, `profiles/default/`).

**DoD / tests.**
- `topic-hub` is discovered, registered, and listed by the launcher/apps API;
  `/a/topic-hub/...` routes resolve.
- Existing (non-hyphen) Apps (`rca`, `playground`) still load unchanged.
- Boot-time coherence checks still pass.

---

## Phase 2 — Collection set as a workspace file + `resolve_collection` (§5)

**Goal.** The Hub's collection set is a workspace file (`collections.json` =
`[{id, name}, …]`); a `resolve_collection` tool lets the agent manage it on user
demand.

**Changes.**
- Define `collections.json` as a seeded workspace file (`[]` initially) + a read
  helper that parses it → `collection_ids` (for the turn-context-builder and for
  workflows via `wf.read_json`). Replaces `wf.config["collections"]` (WF §20).
- New `resolve_collection(ref)` tool: id-or-name → canonical `{id, name}` (candidate
  list on ambiguity / available collections on miss) by looking up the collection
  registry; **resolve only, no write** — the agent writes `collections.json` with its
  file tools.

**DoD / tests.**
- `resolve_collection("equipment log")` → `{id, name}`; unknown ref → miss/candidates.
- The read helper turns `collections.json` into `collection_ids`; a workflow reads
  its `allowed` set from the file.
- Editing `collections.json` (IDE or agent) round-trips; no resource field involved.

---

## Phase 3 — `lookup_glossary` tool (§7)

**Goal.** A deterministic, retriever-free agent tool that returns context cards for a
term over the item's `collections`.

**Changes.**
- New tool `lookup_glossary(term|text)` in the tool registry, implemented over #106
  primitives (`cards_for_collections` + `lookup`/`match`); reads `ctx.context.collection_ids`
  (no `Retriever` needed).
- Populate the App turn's `AgentToolContext.collection_ids` from `collections.json`
  (Phase 2 helper) — the only context wiring required, far less than `kb_search`.

**DoD / tests.**
- `lookup_glossary("M4")` returns the matching card(s); `"M40"` does not (exact
  `norm_keys` membership); empty on miss.
- No LLM / embedding / retriever is touched (unit-level).

---

## Phase 4 — Deterministic context injection `agent.context_files` (§6)

**Goal.** Listed workspace files' live content is prepended to each turn, never
persisted.

**Changes.**
- New `agent.context_files: list[str]` config (app.json / profile manifest).
- In the App turn send-message path, before `engine.stream`, read each listed file
  from the FileStore, wrap in a labelled block, and prepend to the per-turn
  `agent_content` (generalising the #106 idiom). Persisted message stays clean;
  block is re-derived fresh each turn.

**DoD / tests.**
- With `context_files: ["MEMORY.md"]`, the turn handed to the engine carries the
  current `MEMORY.md`; the **persisted** user message does **not**.
- Editing `MEMORY.md` mid-conversation → the next turn reflects the new content
  (freshness); history never accumulates blocks (idempotent / replay-safe).
- Missing file → no block, no error.

---

## Phase 5 — Multiple workflows per profile (§4)

**Goal.** One profile declares N workflows; discovery finds each; the profiles API
exposes them for the picker.

**Changes.**
- `_profile.json`: `workflow` → `workflows: [ {id,title,phases,input_json}, … ]`.
- `workflow/discovery.py`: iterate `profiles/<name>/workflows/<id>/run.py` (file-path
  exec, already the mechanism); validate each manifest's phase ids.
- `GET /a/{slug}/profiles` (WF §14) returns each profile's **list** of workflows.

**DoD / tests.**
- A profile with 2 workflows: both discovered, both validated; a bad `run.py` fails
  boot loud.
- `/profiles` lists both workflows with their manifests.
- A legacy single-`workflow` profile still loads (back-compat shim or migration).

---

## Phase 6 — Multi-chat data model + default-chat back-compat (§3)

**Goal.** Many `Conversation`s per item, with existing item-level endpoints unchanged.

**Changes.**
- `Conversation` gains `id`, `title`, optional `run_id`; keep `item_id` + `messages`.
- `_conversation_for(item_id)` → resolves/creates the **default chat**; existing
  `/messages`, `/stream`, cancel, undo (no `chat_id`) operate on it. Existing stored
  conversations are the default chat (no migration needed).

**DoD / tests.**
- Existing RCA single-chat behaviour is byte-for-byte preserved (default chat).
- An item can hold >1 `Conversation`; each has a stable `id` + `title`.

---

## Phase 7 — Chat-scoped endpoints (§3)

**Goal.** Address, list, and create individual chats.

**Changes.**
- `GET /a/{slug}/items/{id}/chats` (list), `POST .../chats` (create free chat,
  returns `chat_id`).
- Chat-scoped `.../chats/{chat_id}/messages`, `/stream`, cancel — the
  `ChatTurnEngine` keys on `chat_id`.

**DoD / tests.**
- Create two free chats; send to each independently; `/stream` is per-chat.
- Item-level (no `chat_id`) endpoints still hit the default chat.

---

## Phase 8 — Workflow-chat launch + parallel runs (§3, §3.1)

**Goal.** "Run a workflow" opens a workflow-chat; multiple runs can be active in
parallel in one item.

**Changes.**
- Running a workflow **creates** a `Conversation` (`run_id` set) + a `WorkflowRun`
  driving it; returns the `chat_id`. Evolve `POST .../run` (WF §14) accordingly.
- **Lift** WF §14's "one active run per item" → **one run per chat**, many parallel.
- Rely on the existing atomic FileStore writeback for last-write-wins; **no** new
  concurrency control (§3.1).

**DoD / tests.**
- Launching a workflow yields a workflow-chat whose turns stream into it; `human_gate`
  pauses *that* chat; `continue`/decisions resume it.
- Two workflows run concurrently in one item (two chats); both complete; shared
  FileStore writes are last-write-wins.
- A free chat can edit a file a paused workflow chat is waiting on (shared FileStore).

---

## Phase 9 — `create_context_card` capability (§8)

**Goal.** A deterministic node can reliably create a `ContextCard` (decision/action).

**Changes.**
- HTTP capability `create_context_card(collection, {keys,title,body})` (like
  `ingest_to_collection`, WF §8), reusing #106's author action under
  `rm.using(user=<captured>)`; writes a `step_<name>/<key>` receipt.

**DoD / tests.**
- A node creates a card on an existing collection; re-run is idempotent (receipt
  skip); missing collection → error.

---

## Phase 10 — The Topic Hub App, composed (§9–§11)

**Goal.** `apps/topic-hub/` is the full App: file workspace + sandbox, the agent
tools, `context_files`, memory seeding.

**Changes.**
- `app.json`: `function.workspace:true`, `function.sandbox:true`; `agent.tools`
  ceiling = file tools + `lookup_glossary` + `resolve_collection` + `ask_knowledge_base`
  (+ data tools); `agent.context_files:["MEMORY.md","collections.json"]`;
  `item.noun:"Topic Hub"`; layout for `members`/`topics`.
- `model.py`: redeclare `members`/`topics`. (Collection set is a file, not a field — §5.)
- `prompts/system.md`: memory-each-turn + `memory/` on demand + `lookup_glossary` +
  `ask_knowledge_base` guidance (§9).
- `profiles/default/`: seed `MEMORY.md`, `memory/`, and `collections.json` (`[]`);
  declare the workflows (Phases 11–13).

**DoD / tests.**
- Create a Hub: workspace seeds `MEMORY.md`/`memory/`; a free chat answers using
  injected memory + `lookup_glossary`; `ask_knowledge_base` reachable.
- Retrieval layering (§11) observable: a card-covered term needs no RAG.

---

## Phase 11 — `→memory` workflow (§12)

**Goal.** Digest uploaded material into memory files.

**Changes.** `profiles/default/workflows/memory/run.py`: agent nodes read + summarise
uploads, write `memory/*.md`, refresh `MEMORY.md`; gates verify non-empty output.

**DoD / tests.** Upload files → run → `MEMORY.md` + `memory/*.md` produced; re-run
skips unchanged steps (WF §9).

---

## Phase 12 — `→collections` workflow (§12)

**Goal.** The canonical produce → review → commit, with the review content in files.

**Changes.** `workflows/collections/run.py`:
1. **classify** (agent, per file): pick a collection from `collections.json` (§5)
   (`check.choice_in`), digest, collect unknown terms → `plan/<f>.json`.
2. **glossary** (agent): write unknown terms to `glossary.todo.md`.
3. **`human_gate`** (yes/no): "filled the glossary? continue?" (content lives in the
   file; gate stays simple).
4. **commit** (deterministic): `ingest_to_collection` + `create_context_card` per
   filled entry; `check.collection_has`.

**DoD / tests.** End-to-end: classify → gate pause (`awaiting_human`) → human edits
`glossary.todo.md` (or a sibling chat helps) → continue → docs ingested + cards
created; `reject` commits nothing.

---

## Phase 13 — `→consolidate` workflow (§12)

**Goal.** Tidy memory from current memory + recent chats.

**Changes.** `workflows/consolidate/run.py`: read `memory/` + recent messages,
rewrite memory files (dedupe/merge/summarise/drop stale). Run-triggered (no
scheduler).

**DoD / tests.** Run → memory files rewritten coherently; last-write-wins on
`memory/`; stale entries dropped.

---

## Phase 14 — Frontend (FE `/tdd`, vitest)

**Goal.** The Topic Hub UI: multi-chat shell + the App surfaces.

**Changes (may split into further integer phases as needed).**
- **Multi-chat shell** (general): chat list per item, **new-chat picker** = `[Free
  chat]` + the profile's workflows; per-chat stream; reuse `AgentEntryView`.
- **Topic Hub surfaces**: collection-set editor (the `collections` field), memory file
  view (IDE), the glossary fill-in file + a **Continue** affordance on a paused
  workflow chat.
- TanStack Query for reads; SSE per chat stays imperative; wrap tested
  components with `QueryWrap`.

**DoD / tests.** Open a Hub; start a free chat + a workflow chat; a paused workflow
shows Continue; editing the collection set persists; vitest green; `pnpm run
typecheck` + `build` clean.

---

## Final gate (per phase + at the end)

- Backend: `uv run coverage run -m pytest && uv run coverage report` (no pipe-mask),
  100% on new modules; `uv run ruff check && uv run ruff format --check`;
  `uv run ty check`.
- FE: `cd web && pnpm run typecheck && pnpm run build` + vitest.
- A **live** check per LLM-touching phase (fake-LLM tests ≠ feature works).
