# Permission model (#262)

Design of record for `#262 權限管理`. Resolved through `/grill-me`; this file is
the canonical spec. #262 is delivered as a sequence of PRs (see *Rollout*).

The whole thing rests on one slogan: **the AI and everything it reads are
untrusted; the only thing that decides access is the server-side `authorize()`
layer.** Permission is never gated by asking the agent nicely.

## Resources & the shared `Permission`

One `Permission` value object is embedded on every protected resource —
**Collection**, **WorkItem** (app item), **KbChat** — so enforcement and the
editing UI are written once, generically (mirrors the `WorkspaceShell` "one
generic shell, per-App data" philosophy).

```python
Subject = str  # "user:<id>" | "group:<id>" | "all"

class Permission(Struct):
    visibility: Literal["public", "restricted", "private"] = "public"
    read_meta:        list[Subject] = []
    write_meta:       list[Subject] = []
    read_content:     list[Subject] = []
    add_content:      list[Subject] = []
    edit_content:     list[Subject] = []
    read_chat:        list[Subject] = []
    converse:         list[Subject] = []
    execute:          list[Subject] = []
    use_terminal:     list[Subject] = []
    change_permission: list[Subject] = []
```

- `owner` is **not** a field — it is `created_by` (specstar meta), implicit and
  **non-removable**.
- The grant lists **always persist**; `visibility` decides whether they are
  enforced, so toggling public↔restricted↔private never loses settings.

### `visibility` — the three states the owner toggles in the UI

| state | meaning | grant lists |
|---|---|---|
| `public` | open to everyone (every data verb) | kept, **dormant** |
| `restricted` | enforce the per-verb grant lists | **live** |
| `private` | only `owner` (+ superuser) | kept, **dormant** |

A resource with **no `Permission` object at all** ≡ `public` (back-compat for
rows written before #262 — no migration). Create-time defaults: Collection /
WorkItem → `public`; KbChat → `private` (a present-but-empty `Permission`).

## Verbs

| verb | meaning |
|---|---|
| `read_meta` | see it exists + read its fields. **The entry gate.** |
| `write_meta` | edit domain fields (status / severity / collection settings) |
| `read_content` | read content (workspace files / docs) |
| `add_content` | upload **new** content, no overwrite |
| `edit_content` | overwrite + update + delete (**⊇ `add_content`**) |
| `read_chat` | read the conversation thread |
| `converse` | send a message that drives the agent |
| `execute` | run code in the sandbox — the agent's `exec` tool **and** run-python share this one verb (grants the file side-effects too) |
| `use_terminal` | a human opens the shell pane directly (human-only) |
| `change_permission` | edit this resource's `Permission` (visibility + all lists, incl. `change_permission` itself → delegable) |

**Prerequisite graph:** `read_meta` gates everything (no `read_meta` → the
resource does not exist for you); `edit_content ⊇ add_content`. Otherwise the
verbs are **orthogonal** (you can `converse` without `read_content`, `read_chat`
without `converse`, etc.).

## Who can do what

A request runs as one or two principals; the effective decision is:

| actor | effective permission |
|---|---|
| **human, direct** | their own grants on the resource |
| **superuser** (configured user-id set) | everything, everywhere — bypasses `Permission` **and** the `read_meta` gate |
| **owner** (`created_by`) | implicit `change_permission` on their own resource; non-removable safety floor |
| **AI in a turn** | `preset ceiling` ∩ `speaker` |
| **background job** (IndexJob/WikiJob) | `preset ceiling` ∩ `job initiator` |

- **`change_permission`** = `owner` ∪ `superuser` ∪ subjects granted
  `change_permission`. All of them may delegate it further; none can lock out
  `owner`/`superuser`.
- **Hard bars on the AI** (enforced in `authorize()`, never via prompt): the AI
  can *never* be granted `change_permission` or `use_terminal`, regardless of the
  preset ceiling or who drives it — including a superuser. So a prompt-injection
  can at worst make the AI do data verbs the speaker already has; it can never
  rewire access control. A superuser's *direct* actions are unrestricted, but the
  AI they drive is still ceiling-bounded.

### Preset ceiling

The AI's own capability cap is a per-`Preset` verb allow-list (the permission
cousin of `allowed_tools`), carried `Preset → AgentConfig` and read per turn —
default broad, but `change_permission`/`use_terminal` are never in it.

### The entry-gate rule for async work

Authority is checked **at the action boundary** (e.g. the upload's
`add_content`); the async continuation (index, embed, wiki) is part of that
already-authorized action and is **not re-checked**. Background workers run as
their recorded initiator through the same `authorize()`; they are real
principals, never an `if is_system: skip` special case.

## Enforcement

One central `authorize(actor, verb, resource) -> bool`, called from:

1. **The specstar CRUD layer** — Collection / WorkItem / SourceDoc reads &
   writes go through auto-generated `GET/PUT/DELETE` routes, so enforcement hooks
   there, not per-handler.
2. **Hand-written routes** — KbChat, uploads, messages, mentions.
3. **Agent tools** — `read_file`/`write_file`/`edit_file`/`delete_file`/`exec`/
   `kb_search`/`ask_knowledge_base`, each checking `ceiling ∩ speaker` before
   touching a resource.

**Denial:** no `read_meta` → **404** (existence is hidden); have `read_meta` but
lack the verb → **403**. List endpoints filter by `read_meta`.

**List filtering** uses specstar's indexed `list[str]` `.contains` on
`permission.read_meta` (only this verb needs an index — everything else is checked
in Python on the already-loaded resource). Requires **specstar ≥ 0.11.10**, whose
nested-path list-type auto-detection removes the 0.11.9 footgun (a nested indexed
list silently degrading to substring `LIKE`).

**Audit:** permission changes emit a `Notification` (`actor` = who changed it),
reusing the KbChat-share precedent.

## Cross-resource (transitive) checks — KB chat collections

`KbChat.collection_ids` (and the RCA agent's `ask_knowledge_base`) point at
other resources; using them is gated against the **current speaker**, in three
places — all the same `read_content` × the same `authorize()`:

1. **picker** — `GET /kb/collections` is filtered to collections the user can
   `read_content`.
2. **attach** — `create_chat` / changing `collection_ids` re-checks
   `read_content` per id server-side (never trust the body).
3. **converse-time** — retrieval is limited to `collection_ids ∩ what the current
   speaker can read now` (handles shared chats with a different reader, and
   permissions tightened after attach).

Content already baked into past messages is governed by the chat's own
`read_chat` (derived content follows the resource it lives on), not re-checked
against the source collection.

## Rollout (one issue, several PRs)

| PR | scope | behaviour change |
|---|---|---|
| **1** | `Permission` model + verb enum + `Subject` + `Actor` + central `authorize()` (the preset ceiling enters as an `Actor.ai(ceiling=…)` argument) + this doc. **Self-contained `perm/` package — not wired to any resource/route/index/config.** | **none** (dormant; safe to merge) |
| **2** | Collection enforcement: specstar-CRUD hook + routes + `read_meta` list filter + the KB-collection 3 gates | collections enforced (public default → no change until restricted) |
| **3** | App-item enforcement: item routes + agent file/`exec` tools (`ceiling ∩ speaker`) + the per-`Preset`/`AgentConfig` ceiling field that feeds it + `ask_knowledge_base` transitive check | items enforced |
| **4** | KbChat enforcement: migrate `owner`/`shared_with` → `Permission` + converse-time collection filter | KbChat enforced |
| **5** | Background workers (entry-gate) + `use_terminal` gating | workers/terminal enforced |
| **6** | Owner UI to toggle `public`/`restricted`/`private` + edit grants; `Notification` audit | owners can tighten |

**Deferred to follow-up issues:** a first-class logical `Group` entity + the
"which group sees which collections" governance UI (the `group:` Subject
namespace is reserved now so no data migration later); per-document permissions;
full SSO (identity is already real via `get_user_id`).
