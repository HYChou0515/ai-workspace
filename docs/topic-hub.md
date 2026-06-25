# Topic Hub — the manual

> **Status:** normative design spec for the **Topic Hub** App (working issue TBD).
> This document is the *target* and the *acceptance criterion*: the implementation
> is "done" when its observable behaviour matches the rules here. Written before the
> plan, on purpose ("以終為始"). Decisions were locked through a `/grill-me` session;
> rejected alternatives are recorded inline so we don't relitigate them.
>
> Topic Hub builds on, and is mostly composed from, four existing pieces:
> **#89 Apps** (`docs/adding-an-app.md`), **#100 Workflows** (`docs/workflows.md`),
> **#106 Context Cards** (`docs/plan-context-cards.md`), and the **#43 collab** chat
> (already in `master`). The genuinely *new* platform work is small and is called
> out explicitly in §2.

A **Topic Hub** is a workspace for a sustained, **cross-collection** line of inquiry
that **accumulates knowledge over time**. You drop in material, chat about it (alone
or with others), and run workflows that distil it into durable **memory** files and
file your documents into the right KB **collections** — all inside one item that you
come back to across sessions.

The key framing, locked in grilling:

- **Topic Hub is an App** (`apps/topic-hub/`), not a new first-class "Topic"
  resource. It leans on the entire #89 App platform (launcher, item list, create
  flow, file workspace, agent, profiles, members, mentions) and the #100 workflow
  platform, and adds a handful of small, **general** platform bricks (§2).
- **The item is a container; the *topic* is its content.** One Hub holds chats,
  memory files, and collection references that are *about* some subject — the Hub
  itself is not "a topic."
- **Memory is files. The collection set is a file. Chat is a workflow** (UI-wise).
  **Group chat is inherited** from #43. None of these needs reinventing.

---

## 1. Mental model

- **One App, one item per Hub.** `apps/topic-hub/` is a normal #89 App. Each item is
  one Hub: a file workspace + an agent + (now) **many chats** + a **collection set
  (a workspace file)** + **memory files**.
- **chat 即 workflow — "two modes of one engine".** Every chat is a run of the *one*
  `ChatTurnEngine` (workflows.md §1/§10). Some chats are **orchestration-driven** (a
  workflow's `run()` is driving the turns); some are **human-driven** (free chat). The
  UI is identical; "open a new chat" is a picker of **[free chat]** or **[a workflow
  type]**.
- **Everything durable is files.** Memory (`memory/` + `MEMORY.md`), the collection
  set (`collections.json`), workflow artifacts, the glossary fill-in — all workspace
  files, curated by workflows and editable by humans + the agent. A small index file
  (`MEMORY.md`) is the always-in-context core (§6).
- **Retrieval is three layers, cheapest first** (§11):
  1. **memory** — always injected (`MEMORY.md`) + deeper files read on demand;
  2. **context cards** — deterministic, fast (`lookup_glossary` tool + #106 route
     injection); covered terms answered with no RAG;
  3. **docs / wiki** — `ask_knowledge_base` (heavy; the *rare* path now).

Why this shape: the user's KB stack can't disable reasoning on its prod vLLM, so
`kb_search`/`ask_knowledge_base` are slow (the original #106 pain). Pushing the common
case onto deterministic memory + cards means the slow agentic retrieval is the
exception, not every turn.

---

## 2. What is inherited vs what is new platform

**Inherited for free** (no new work — Topic Hub just *uses* it):

| From | What |
| --- | --- |
| #89 Apps | launcher card, item list, create flow, **file workspace + IDE + file tools**, agent + **per-profile system prompt**, profiles (seeded files / skills / prompt), `members` + `topics` (Tier-2), 3-layer agent-config resolve, `function.*` toggles |
| #100 Workflows | `run()` orchestration, agent + deterministic nodes, **`human_gate`**, the **filesystem-journal + input-hash** execution model, `WorkflowRun`, capabilities (`ingest_to_collection`), run-scoped credential, Run/Poll/Stream/Decide API, phase diagram |
| #106 Context Cards | `ContextCard` (`norm_keys`), `lookup`/`match`/`cards_for_collections`/`card_context_block`, the route-layer injection idiom |
| #43 collab (in `master`) | **multi-writer group chat** (no owner gate on App-item `send_message`, `author`-stamped, **broadcast to live viewers**, `/stream`), `mention` + notify |

**New platform bricks** — small, **general** (any App may use them):

- **(I) Multi-chat per item** — §3. *Platform-wide* (all Apps, incl. RCA).
- **(II) Multiple workflows per profile** — §4.
- **Collection set as a workspace file** (not a resource field) + a `resolve_collection`
  tool to manage it on user demand — §5.
- **Deterministic context injection** (`agent.context_files`) — §6.
- **`lookup_glossary`** — a context-card-only retrieval tool — §7.
- **`create_context_card`** — a workflow capability (decision/action) — §8.

**Topic Hub** (§9–§13) is then the App that *composes* these bricks.

> **Slug note.** The chosen slug is **`topic-hub`** (hyphenated). The current App
> platform ties `slug == dir == importable package` (`apps.<slug>.model`, no hyphens
> per `adding-an-app.md`). Supporting a hyphenated slug means **loading the App's
> `model.py` by file path** — exactly how workflow `run.py` and hyphenated profile
> dirs are *already* loaded (`workflow/discovery.py`) — instead of `import_module`.
> This is a one-function platform tweak, recorded here as a decision.

---

## 3. Multi-chat (platform-wide)

Today an item has **exactly one** `Conversation` (`_conversation_for` is
get-or-create by `item_id`) and a workflow run drives that sole conversation
(workflows.md §1/§10). Topic Hub needs **many concurrent chats** per item.

- **Data model.** `Conversation` becomes the base unit and gains `id`, `title`, and
  an optional `run_id`; **many per item**. A **workflow chat** = a `Conversation`
  plus a `WorkflowRun` (workflows.md §13) **driving** it; a **free chat** = a
  `Conversation` with **no** run. This is "two modes of one conversation"
  (workflows.md §1/§10) made concrete: the run is an *overlay* on a conversation, not
  a separate entity.
  - *Rejected: "everything is a `WorkflowRun`" (free chat = a degenerate run).* A
    `WorkflowRun` carries phase/manifest/`pending_decision` fields that are
    meaningless for free chat — forcing them empty is noise. Conversation-as-base
    keeps free chat trivial.
- **Scope: platform-wide.** Multi-chat is enabled for **every App** (RCA included),
  not a per-App opt-in.
  - *Rejected: per-App opt-in toggle.* The user chose global for consistency; the
    backward-compat default chat (below) keeps non-multi-chat Apps unchanged anyway.
- **Backward compatibility: implicit default chat + additive endpoints.** Each item
  keeps an implicit **default chat**; the existing item-level endpoints (`/messages`,
  `/stream`, cancel, undo) with **no** `chat_id` resolve to it, and existing data is
  that default chat. New **chat-scoped** endpoints (`/items/{id}/chats`,
  `/items/{id}/chats/{chat_id}/...`) are added for the multi-chat surface.
  - *Rejected: full chat-scoped refactor + data migration.* Additive is far less
    churn; clients and stored conversations keep working untouched.
- **Launching = opening a chat.** "Run a workflow" **creates a workflow-chat** and
  returns its `chat_id`. workflows.md §14's "at most **one** active run per item" is
  **lifted**: runs are now **one per chat**, and many can be active in parallel (§3.1).

### 3.1 Concurrency

- **Parallel runs are allowed** — multiple chats (free or workflow) may be active at
  once in one Hub.
- **All chats of a Hub share one durable `FileStore`** (the truth). A chat in one
  thread (e.g. a helper free chat) edits the very file a paused workflow chat is
  waiting on — that is the point.
- **Last-write-wins, atomically.** Sandbox writeback to the `FileStore` goes through
  specstar's content-addressed `write` (new blob → atomic file-id swap), so a
  whole-file overwrite is atomic — **no torn writes**, even under concurrency. The
  no-live-sandbox direct-edit path is *stronger still* (etag-guarded CAS:
  read→write→retry, reports a conflict under persistent contention —
  `tests/files/test_facade_cas.py`).
- **No special cross-chat concurrency control.** Two runs slamming the *same* file at
  the same instant should not happen in normal use; if it does, let it fail. The
  durable record stays consistent because the writeback is atomic.
- **Step namespaces stay disjoint.** Each workflow's journal lives in its **own
  folder** `/.workflow/<workflow_id>/` (#136), and within it parallel writes land under
  their own `step_<name>/<key>` namespaces (workflows.md §9); only **intentionally
  shared** files (e.g. `memory/`, `collections.json`, a glossary fill-in file)
  overlap, and there last-write-wins is the *intended* behaviour. Authors keep this in
  mind; the platform does not police it.

---

## 4. Multiple workflows per profile

workflows.md §2 fixes "a profile has **0 or 1** workflow" and treats "workflow ==
profile". Topic Hub needs one item (seeded from one profile) to offer **several**
workflow types (e.g. `→memory`, `→collections`, `→consolidate`).

- **`_profile.json` carries a list.** The single `workflow` block becomes
  `"workflows": [ { "id": "...", "title": "...", "phases": [...], "input_json": "..." }, ... ]`.
  Each workflow has its own orchestration at **`profiles/<name>/workflows/<id>/run.py`**.
  Discovery (`workflow/discovery.py`) iterates these by file path (it already
  file-path-execs `run.py`, so the change is iterating a directory).
- **One profile, one behaviour package, N workflows.** All of a profile's workflows
  share that profile's **tool ceiling**, **seeded files**, and **prompt** assets
  (amends workflows.md §2's "one complete behaviour package" to allow N workflows;
  §18's "profile = immutable behaviour version" extends to "a version that offers N
  workflows").
- **The "new chat" picker** lists **[free chat]** + the seed profile's N workflows.
- *Rejected: an App-level workflow catalog* (`apps/<slug>/workflows/`, any item runs
  any). Cleaner in the abstract, but it decouples workflows from the seed profile and
  scatters per-workflow tool ceilings; the user asked for "multiple workflow types **in
  one profile**", and profile-level is the smaller, same-shape change.

---

## 5. Collection set — a workspace file

The Hub's set of collections is a **workspace file** (`collections.json`, a list of
`[{id, name}, …]`), **not** a field on the item's resource. This keeps everything in a
Hub file-shaped (like memory) and the `WorkItem` thin; it is mutable any time by the
**collection picker** (§5.2), the agent (§5.1), or — as an escape hatch — by editing
the raw file in the Monaco IDE.

- **Read at turn/run time, not from the resource.** Workflows read it with
  `wf.read_json("collections.json")`; the App turn-context-builder reads it to
  populate `collection_ids` for retrieval (`lookup_glossary`, `ask_knowledge_base`).
  The `→collections` workflow's `allowed` set **is** this file (replacing WF §20's
  `wf.config["collections"]`).
- **One set, two roles** (locked): the **read scope** for the Hub's chats *and* the
  **write candidates** the `→collections` workflow files into
  (`check.choice_in(..., allowed=<from file>)`).
- *Rejected: a Tier-3 resource field.* The user moved it to the filesystem for
  everything-is-files consistency. **Accepted tradeoff:** we lose the ability to
  *index/query* "which Hubs reference collection X" via specstar — not needed for v1.
- *Rejected: profile-fixed (workflows.md §20)* and *two separate read/write sets* —
  one mutable file, both roles.

### 5.1 `resolve_collection` — managing the set on user demand

A user changes the set conversationally ("add the equipment-log collection"). They
give an **id or a name**; the agent needs the canonical `{id, name}` pair to record.

- **New tool `resolve_collection(ref)`**: given an id **or** a name, return the
  canonical `{id, name}` — or a candidate list on ambiguity / the available
  collections on a miss — by looking up the collection registry. **It only resolves;
  it does not write.**
- **The agent writes `collections.json` itself** with its file tools (`write_file` /
  `edit_file`), appending or removing the resolved `{id, name}` entries. Interactive
  chat, so a plain file write is fine — no decision/action node needed (that pattern
  is for *workflow* side-effects, §8).
- *Rejected: one tool that resolves **and** writes the file.* Keeping the write as an
  ordinary file edit matches "the agent itself maintains the file" and stays
  consistent with how memory is edited.

### 5.2 The collection picker (#142)

Editing `collections.json` by hand in Monaco is the power-user path, not the everyday
one — so the Hub's chat top bar carries a **collection-set button** that opens a picker
modal. FE-only; the backend (the file, `resolve_collection`, the turn-time read) is
unchanged.

- **Button states (discoverability).** Empty selection → an accent-styled
  **「選擇知識庫」** nudge (a Hub with no collections has nothing for the agent to
  retrieve); non-empty → a quiet **「知識庫 (N)」** badge. It is item-level (the set is
  shared by every chat + the agent), so it sits on the shell bar, not inside a chat.
- **Modal = a checklist over the live collection list** (`GET /kb/collections`) with a
  search box; each row shows the collection's icon, name, and doc count, pre-checked
  from `collections.json`.
- **Display + write-back use LIVE names**, so a renamed collection self-heals and the
  file stays fresh for the every-turn context injection (§6).
- **Persistence = last-write-wins** (the locked "有爆炸就給它爆"): the modal reads the
  file fresh on open and, on an explicit Save, overwrites the whole file (no merge,
  2-space JSON) and invalidates both the picker's read and any open Monaco tab. It
  **never writes on open**.
- **Robustness.** A missing file → an empty selection (no warning); a whole-file parse
  failure → a warning banner (the file may be mid-hand-edit) but Save may still
  overwrite it; malformed entries are tolerated the way the backend's
  `collection_ids_from_json` tolerates them (dropped + counted). An **orphan id** (its
  collection was deleted) is surfaced in its own area with one-click removal and is
  **preserved verbatim** on save until the user removes it — never auto-dropped.

---

## 6. Deterministic context injection (`agent.context_files`)

The Hub's curated memory core (and current collection set) must be in front of the
agent **every turn**, reliably (local small models won't reliably remember to
`read_file` them). #106 already does a *specific* version of this — it prepends
matched context cards to the turn content before `engine.stream`. We **generalise
that idiom into config.**

- **New config field** `agent.context_files` (in `app.json` / a profile manifest): a
  list of workspace files whose **live content** is prepended to the content handed to
  the agent **each turn**, wrapped in a labelled block.
- **Static instruction** ("your memory is below; treat it as current; deeper detail
  is in `memory/`, read it on demand") lives in `prompts/system.md` — plain text, no
  new mechanism.
- **Every turn, live, never persisted.** The block is **re-derived fresh** at
  LLM-call time from the *clean* history + the file's *current* content, and is **not
  stored** in the conversation. Therefore:
  - only the **latest** turn ever carries a block — no accumulation, no N copies;
  - the agent always sees the **current** memory / collection set (both mutate
    mid-session);
  - it is a pure function of `(file content, turn)` → **idempotent and replay-safe**
    (#51 replay re-derives exactly what the LLM saw).
  - *Rejected: "store the block, strip it next turn".* That mutates stored history
    and breaks resume/replay; "never persist + re-derive" gives the identical result
    with none of the risk.
- *Rejected: pull-only* (small models forget to read); *inject-everything* (unbounded
  memory blows context); *prompt-template interpolation `{{file:…}}`* (a new per-turn
  prompt-assembly mechanism; the §106 prepend is proven and simpler — kept as the v1
  placement, with system-prompt placement available later if higher instruction
  altitude is wanted).

---

## 7. `lookup_glossary` — a context-card-only tool

A new **lightweight agent tool** for the common case: look up a term against the
Hub's collection **context cards**, deterministically.

- **Behaviour.** Given a term (or free text), return the matching `ContextCard`s for
  the Hub's collections (read from `collections.json`, §5), via the existing #106
  primitives (`cards_for_collections` + `lookup` exact-key / `match` text-scan). **No
  LLM, no embedding, no retriever, no agentic loop.**
- **Context need is minimal** — only the Hub's `collection_ids` (from the file) +
  spec access to `ContextCard`. It does **not** require a `Retriever` in the
  `AgentToolContext` (unlike `kb_search`), so it is *not* the "force `kb_search` into
  an App context" hack that was rejected (§13-rationale below).
- **Complements #106 route injection.** Route injection scans the *user's message up
  front*; `lookup_glossary` lets the agent look up terms it hits **mid-work** (in a
  file it's reading, in a retrieved doc) and decide to define them.
- *Rejected: wiring `kb_search` into the App turn context.* `kb_search_impl` asserts a
  `retriever` the App runs never set, and RCA deliberately uses `ask_knowledge_base`
  instead. Adding a tiny, retriever-free card tool is cleaner than retrofitting the KB
  retriever onto App contexts.

---

## 8. `create_context_card` — a workflow capability

The `→collections` workflow ends by turning the human-filled glossary into context
cards. Per the workflows.md **decision/action** principle (§4/§8), the agent
**decides** card content *as data*; a **deterministic node** commits it.

- A new HTTP **capability** (like `ingest_to_collection`, workflows.md §8): a sandbox
  deterministic node calls it with the run-scoped credential to **create a
  `ContextCard`** (reusing #106's author action) on a collection in the Hub's set.
- Records a `step_<name>/<key>` receipt so it is checkpointable / idempotent under
  re-run; requires the collection to exist.

---

## 9. The Topic Hub App

`apps/topic-hub/` composes the bricks above:

- **`app.json`**
  - `function.workspace: true` (file IDE + file tools — memory + uploads + the
    collection-set file live here), `function.sandbox: true` (workflow deterministic
    nodes run in the sandbox and call capabilities), `function.terminal` optional.
  - `agent.tools` (ceiling): file tools + **`lookup_glossary`** + **`resolve_collection`**
    + **`ask_knowledge_base`** (+ the data tools the workflows' nodes need).
  - `agent.context_files: ["MEMORY.md", "collections.json"]` (§6) — memory core + the
    current collection set, in front of the agent every turn.
  - `item.noun`: "Topic Hub".
  - layout/labels for `members`/`topics` (the collection set is a file, not a field).
- **`model.py`** — `WorkItemBase` subclass: redeclare `members`/`topics`. (The
  collection set is a workspace file, **not** a model field — §5.) `INDEXED_FIELDS`
  only for any genuine Tier-3 scalars added later.
- **`prompts/system.md`** — "Your memory and current collections are provided each
  turn; treat them as current. Deeper memory is under `memory/` — read on demand. Look
  up unknown terms with `lookup_glossary`. To change the collections, use
  `resolve_collection` then write `collections.json`. For document/wiki content, use
  `ask_knowledge_base`."
- **`profiles/default/`** — seeds `MEMORY.md`, a `memory/` dir, and an initial
  `collections.json` (`[]`); declares the N workflows (§12); ships any prompt/skill
  assets.

---

## 10. Memory model

- **Memory is files.** A `MEMORY.md` index (auto-injected, §6) + deeper notes under
  `memory/`. Built and maintained by workflows (§12); freely editable by the agent
  (file tools) and humans (IDE).
- **Structure is convention, not schema.** The proposal's memory *types* (Fact /
  Hypothesis / Insight / Decision / Goal / Summary) and *confidence* are expressed as
  **file organisation + in-file annotation** (e.g. `memory/decisions.md`, a
  `(unconfirmed)` tag) — decided by the `→memory` workflow's output format, **not** a
  typed specstar resource.
  - *Rejected: a first-class typed `Memory` resource* with confidence + an
    extraction→review lifecycle + a second retrieval system. Far too much machinery
    for v1; what a workflow produces is App implementation (workflows.md §19).

---

## 11. Retrieval layering

A Hub chat answers from three sources, cheapest first:

1. **Memory (always).** `MEMORY.md` injected every turn (§6); deeper `memory/*.md`
   read on demand.
2. **Context cards (deterministic, fast).** `lookup_glossary` (agent, mid-work) +
   #106 route injection (up-front scan of the user message). Covered terms are
   answered with **no RAG**.
3. **Docs / wiki (heavy, rare).** `ask_knowledge_base` over the Hub's collection set
   (from `collections.json`, §5) — the only path that runs the slow agentic KB
   retrieval, now the exception.

- **Deep retrieval uses `ask_knowledge_base`, not `kb_search`** — it is App-available
  out of the box (no retriever wiring), and it is hit rarely because layers 1–2 absorb
  the common case. `kb_search` / `ask_knowledge_base` themselves are unchanged.

---

## 12. Example workflows (the default profile)

*Illustrative — what a workflow does is App implementation (workflows.md §19).*

- **`→memory`** — digest uploaded material into memory files. Agent nodes (read +
  summarise) write `memory/*.md` + refresh `MEMORY.md`. Produce-then-write.
- **`→collections`** — the canonical **produce → review → commit**, with the "review"
  content living in **files**. The agent does the writing (it **drafts** the glossary);
  the human **reviews** a draft rather than filling in blanks (#133):
  1. **classify** (agent, per file): pick a collection from the Hub's set
     (`collections.json`, §5) + write a digest + — while it still has the file open —
     **draft a definition for each unknown term**, flagging each as confident or not →
     `plan/r<round>/<f>.json` (gate: collection ∈ allowed set + `terms` shape).
  2. **cards** (deterministic): assemble the drafts into proposed cards in
     `context-card.todo.md` — one `## <title>` block per term carrying `collection` /
     `keys` / body (a confident draft becomes the body; an uncertain one a `⚠️` line).
     Beside it, write a **read-only "before" snapshot** `.readonly/context-card.current.md`:
     for each proposed card, the existing card a commit-time upsert would **overwrite**
     (its real keys/title/body), or an empty block when the card is new (#205). So the
     two files diff block-by-block and a silent key-narrowing is visible, not hidden.
  3. **`human_gate`** (`approve` / `reject` / `revise`): the gate summary points to
     **查看變更**, which opens a VSCode-style diff — left = `.readonly/context-card.current.md`
     (read-only), right = `context-card.todo.md` (editable) — so an overwrite is never
     blind-signed. The human reviews/edits the proposed cards **in the diff** (or another
     chat for LLM help — shared FileStore, §3.1). **Approve** commits what's in the file
     (incl. their edits); **revise** + a note re-runs the whole produce step to regenerate
     the drafts (overwriting), re-gating; **reject** ends the run for interactive takeover.
  4. **commit** (deterministic, idempotent): `ingest_to_collection` the docs +
     `upsert_context_card` (§8, #111) for each *filled* block — the `collection` is read
     straight from the block (so a human title edit can't misroute it), and an
     unknown collection is rejected loudly. A block that is still only a `⚠️` line is
     skipped (unresolved), so re-classifying a term updates its card, not duplicates it.
- **`→consolidate`** — read current memory + recent chats, **rewrite** memory files
  (dedupe / merge / summarise / drop stale). Self-referential; last-write-wins on
  `memory/`. **Triggered via Run** (a human or an external scheduler hits the Run
  endpoint) — there is **no platform scheduler** (workflows.md §14 already delegates
  periodicity to the caller). With multi-workflow (§4) this is just another workflow
  type, not a special mechanism.

---

## 13. Group chat & visibility

- **Group chat is inherited** (#43, already in `master`): any authenticated user can
  send into a Hub's chats, messages are `author`-stamped, broadcast to live viewers
  (`/stream`), and `mention` + notify works. **No new work.**
- **v1 visibility = platform default:** all internal authed users can access; the
  existing sharing/mention mechanisms apply. **No per-item ACL** and **no
  private/team/org scopes** — deferred until real SSO/authz lands (the proposal's open
  Q4).

---

## 14. Platform vs App boundary

- **Platform bricks (general, reusable):** multi-chat (§3), multiple workflows per
  profile (§4), the collection-set workspace file + `resolve_collection` tool (§5),
  `agent.context_files` deterministic injection (§6), the `lookup_glossary` tool (§7),
  the `create_context_card` capability (§8), and the hyphenated-slug file-path App
  loader (§2 note).
- **The Topic Hub App composes them** (§9–§13): its `app.json` / `model.py` /
  `system.md`, the three example workflows, the memory + collection-set file
  conventions, and the retrieval layering.

---

## 15. Phasing & non-goals

**v1 (what Topic Hub needs):**
the platform bricks of §2 (multi-chat with default-chat backward-compat + parallel
runs; multiple workflows per profile; the collection-set file + `resolve_collection`;
`context_files` injection; `lookup_glossary`; `create_context_card`; hyphenated-slug
loader); the `apps/topic-hub/` App (§9); memory-as-files (§10); the three example
workflows (§12); group chat inherited as-is (§13).

**Deferred / non-goals:**

- **Per-item ACL / visibility scopes** (private / team / org) — wait for SSO/authz.
- **A platform scheduler** for periodic consolidation — periodicity is the caller's
  job (workflows.md §14); `→consolidate` is Run-triggered.
- **A typed `Memory` resource** (types/confidence as schema, extraction→review
  pipeline, a separate Memory-Retrieval system) — memory is files; structure is
  convention.
- **Knowledge-graph memory** (entities / relationships / evidence) — the proposal's
  open Q5; not v1.
- **kb_search latency / reasoning-off on prod vLLM** — a separate, already-deferred
  issue; Topic Hub *mitigates* it (layers 1–2) but does not fix it.
- **Steer-and-resume** mid-run interjection — inherits workflows.md's deferral.
