# Plan — Context Cards (#106)

> A lightweight, **deterministic** glossary path that lives *alongside* `kb_search`
> (NOT a replacement). `kb_search` is slow on prod because reasoning can't be
> disabled across GLM/Qwen/Gemma on vLLM (every expand/rerank/agent turn `<think>`s)
> and the agent loops 3–5 searches. We do **not** fix that here — kb_search perf +
> per-model/vLLM reasoning-off is a **separate later issue**. This issue adds context
> cards: a term → explanation lookup answered by an exact, no-LLM, no-embedding DB
> query. Follow `/tdd` per phase (FE too, vitest). Gate at the end: full suite + 100%
> coverage (no pipe-mask), `ruff`, `ty`, FE typecheck + build.

## Scope

**In:** `ContextCard` resource on a Collection; deterministic exact-key lookup
(`get`, batch, exposed) + free-text scan (`match`, internal); CRUD + a FE tab; inject
matched cards into the KB chat agent.

**Out (deferred):** kb_search latency / reasoning-off on vLLM; RCA-agent injection;
fuzzy/semantic matching; third-party API-key auth (reuse existing app auth).

## Design (locked in grill)

```
ContextCard (specstar Struct, → resource "context-card", attached to a Collection)
  collection_id: Ref("collection", cascade)   # indexed — list/scope/match-load
  keys:      list[str]                         # author surface forms: ["M4","Metal 4","capping"]
  norm_keys: list[str]                         # DERIVED + indexed; server-owned, never hand-edited
  title:     str = ""                          # display; "" → keys[0]
  body:      str = ""                          # markdown explanation (plain str, not Binary)
```

- `norm(s) = " ".join(unicodedata.normalize("NFKC", s).casefold().split())` —
  deliberately simple so external callers replicate it. No prefix/fuzzy.
- **key ↔ card is many-to-many** → every lookup returns `list[ContextCard]`. No
  key-uniqueness constraint.
- **Core primitive (both consumers):** `QB["norm_keys"].contains(norm(q))` — `.contains`
  on a `list[str]` is **exact element membership** (so `"M4"` ≠ `"M40"`), the same
  index path already used by `KbChat.shared_with`. Single resource, no join table.

**`norm_keys` derivation = specstar custom actions** (`@spec.create_action` /
`@spec.update_action`, [specstar howto/routes](https://hychou0515.github.io/specstar/howto/routes/#custom-update-actions)).
The handler receives the author input (create) or the injected `existing` + patch
(update), returns a `ContextCard` with `norm_keys=derive_norm_keys(keys)`, and
specstar persists it via `rm.create()` / `rm.update()` in the **same write** — no
event handler, no write-back loop, derivation can't be bypassed by the FE because the
FE authors through these actions. (These are CRUD-layer routes → P2.)

**Open decision — resolved as default (cheap to flip):**
- `title` **kept** (friendly display name distinct from a key; falls back to `keys[0]`).

## Key existing seams to hook (don't rebuild)

- **`KbChat.shared_with`** — precedent for an indexed `list[str]` queried with
  `QB[...].contains(x)` (`resources/__init__.py` `add_model(KbChat, indexed_fields=["shared_with"])`,
  `api/kb_chat_routes.py:295`). `norm_keys` copies it verbatim.
- **specstar auto-CRUD** — `add_model` emits CRUD routes; don't wrap to hide them
  ([[feedback_specstar_routes_fine]]). FE list/create/update/delete ride these.
- **`event_handlers.extend(...)`** post-`add_model` — `kb/index_coordinator.py` is the
  pattern; scope to `on_success(patch)`, swallow exceptions ([[reference_specstar_event_handlers]]).
- **#87 monaco editor** (`feat/issue-87-kb-doc-ide`) — reuse the FE editor *component*
  for `body`; backend stays a plain `str` (no FileStore plumbing).
- **KB chat turn build** (`api/kb_chat_routes.py` + `api/turns.py ChatTurnEngine`,
  KB `AgentToolContext`) — the pre-pass injection point for `match(text)`.
- **`Ref(..., on_delete=cascade)`** — cards die with their Collection (like SourceDoc).
- **pydantic response models** for the lookup endpoint ([[feedback_pydantic_response_models]]).

---

## Phases (flat; FE/CRUD pulled early so cards can be hand-authored to test)

### P1 — `ContextCard` resource + `norm()` + `derive_norm_keys()` (pure core)
- `ContextCard` Struct in `resources/kb.py`; register
  `add_model(ContextCard, indexed_fields=["collection_id", "norm_keys"])` in `make_spec`.
- `kb/context_cards.py`: `norm(s)` and `derive_norm_keys(keys) -> sorted({norm(k)…})`.
- **TDD:** `norm()` cases (casefold / NFKC full-width / whitespace collapse);
  `derive_norm_keys` sorted + unique + drops blanks; resource registered + create/get
  roundtrip; cascade-delete with Collection.

### P2 — CRUD via custom actions + FE "Context Cards" tab
- Backend: `@spec.create_action("context-card", path="author")` +
  `@spec.update_action("context-card", path="edit", mode="update")` whose handlers set
  `norm_keys=derive_norm_keys(keys)` (the only write path the FE uses → derivation can't
  be bypassed). **No hand-rolled read route** — list/get/delete ride specstar's auto
  CRUD; the FE lists a collection's cards via `GET /context-card?qb=QB['collection_id']
  == '<cid>'` (collection_id indexed → a query, not a scan) and reads the specstar
  envelope (`item.data`, `item.revision_info.resource_id`). The create-action module
  must NOT use `from __future__ import annotations` (specstar resolves the action body
  type at apply()-time and can't follow stringised ForwardRefs).
- FE: a "Context Cards" tab on the collection page — left list (`useQuery`, key in
  `queryKeys.ts`), center **monaco** (reuse #87) for `body` + a keys **tag** editor +
  **New**; save = `useMutation` (POST the create/update action) + `invalidateQueries`.
  `useCurrentUser` not hardcoded.
- **TDD (backend + vitest):** create action → `norm_keys` derived & sorted-unique;
  update action → recomputed from new `keys`; FE never sends `norm_keys`; list returns
  only the collection's cards; delete roundtrip; empty-state New.

### P3 — `get(term)` deterministic query core
- `kb/context_cards.py`: `lookup(spec, collection_id, terms) -> dict[str, list[ContextCard]]`
  — per term `(QB["collection_id"] == cid) & QB["norm_keys"].contains(norm(t))`; key the
  result by the **original input term**. (If `terms` is large, OR-combine the `contains`
  into one query + group in Python — `&`/`|` both supported.)
- **TDD:** exact hit; one key → multiple cards; one card → multiple keys; normalization
  (case / full-width / whitespace); miss → `[]`; `"M4"` does NOT return the `"M40"` card
  (membership, not substring); other collection's card excluded.

### P4 — external lookup API
- `POST /collections/{cid}/context-cards/lookup` body `{terms: [...]}` →
  pydantic `{results: {term: [Card...]}}`; reuse existing app auth (internal authed
  users/services). Card response model = `id, title, keys, body`.
- **TDD:** batch; unknown collection → 404; empty `terms` → empty; auth required;
  response matches the pydantic schema.

### P5 — `match(text)` single-pass primitive
- `kb/context_cards.py`: `build_vocab(cards) -> {norm_key: [Card]}`; `_word_ascii`;
  `_hits(nt, key)` (locate via `str.find`, accept an occurrence whose edges aren't
  glued to an ASCII word-char `[A-Za-z0-9_]`); `match(text, vocab, cap=10)` — one pass
  over vocab, dedupe cards, stable sort, cap. (Aho-Corasick is a later data-structure
  swap if vocab explodes — still one pass; **not** a second pass.)
- **TDD (boundary table):** `m4` rejects `m40`; `etch` rejects `foobar_etch`; CJK key
  matches embedded (`封蓋製程` in `這個封蓋製程的問題`); multi-word `metal 4`; standalone
  `m4`; dedupe (card hit by 2 keys once); cap + deterministic truncation order; empty.

### P6 — inject matched cards into the KB chat agent
- In the KB chat turn setup: pre-pass `match(user_message, vocab_of(collection_ids))`,
  inject hit cards as a labelled context block into the agent context (hit → answered
  from the card; miss → normal `kb_search`, unchanged). Respect the cap; `log` drops.
- **TDD (`ScriptedAgentRunner`):** a term with a card → its body present in the agent
  context, no `kb_search` needed; no hit → no injection, `kb_search` path intact; cap.

### Gate (end)
Full suite + 100% coverage (no pipe-mask, per [[feedback_gate_no_pipe_mask]]), `ruff
check` + `ruff format --check`, `ty check`, FE `typecheck` + `build`. Live canned check:
author a card, `get` it via the API, and confirm a KB-chat term question answers from
the injected card without a `kb_search` round-trip.
