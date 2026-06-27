# #262 PR2 (collection enforcement) — handoff

Resume point for a fresh session. The canonical design is
[`plan-permissions.md`](./plan-permissions.md) (merged in PR 1). This file is the
**PR-2-specific state + what remains**.

## Where things stand

- **PR 1 (#282) — MERGED.** The `perm/` package: `Permission` value object +
  `Actor` + central `authorize(actor, verb, permission, *, created_by,
  superusers)`. Pure model, fully tested. Read `src/workspace_app/perm/` first.
- **PR 2 — in progress** on branch `worktree-issue-262-collection-enforce`
  (worktree `.claude/worktrees/issue-262-collection-enforce`). 3 commits; 2308
  unit tests pass, zero regressions. **Not yet PR'd / merged.**

## The hard part is solved: enforce via specstar `access_scope` (≥ 0.11.11)

A spike proved specstar's `before` permission-checker and `on_success` event
handlers **cannot** enforce the auto-CRUD HTTP routes (before-get has no resource
data; the envelope GET `_handle_get_with_returns` bypasses event-emitting reads).
So I filed **specstar #398**; the author shipped it as **#399/#400/#401**,
published **v0.11.11**. The mechanism is now:

- **`access_scope`** (read/list visibility + write precondition) — `add_model(M,
  access_scope=lambda user: ConditionBuilder | None | UNRESTRICTED)`. specstar
  ANDs it into every read (all GET variants + list/search/count) **and** gates
  every write that targets an existing row, **at the storage layer**. Out of
  scope → **404** (before the checker; no existence leak). Internal
  `ResourceManager` calls are unscoped; custom routes opt in with
  `rm.using(user, apply_access_scope=True)`. `UNRESTRICTED` = see-all (superuser).
- **`permission_checker`** (per-verb authorization → **403**) — runs only for
  in-scope rows. With #399/#401 the write/lifecycle contexts now carry
  `current_resource` (data + meta).
- They compose: `access_scope` = "does this row exist for me?" (404);
  `permission_checker` = "may I do this action?" (403). Docs:
  `specstar/docs/en/howto/access-scope.md`.

## What's DONE on the branch

- `Collection.permission: Permission | None = None` (`resources/kb.py`) —
  `None` ≡ public (no migration).
- `perm/scope.py::collection_access_scope(superusers)` — the visibility
  predicate mirroring `authorize(read_meta)`:
  `permission.visibility IS NULL | == 'public' | created_by == user |
  (== 'restricted' & permission.read_meta contains_any [user:<id>, all])`;
  superuser → `UNRESTRICTED`.
- Registered in `resources/__init__.py`: `add_model(Collection,
  indexed_fields=[("permission.visibility", str), ("permission.read_meta",
  list)], access_scope=collection_access_scope(superusers))`. `make_spec(...,
  superusers=frozenset())` threads the set.
- `kb_routes.list_collections` filters the hand-written `/kb/collections` list in
  Python via `authorize(read_meta)` (`_can_read_meta`). (Could instead opt the
  aggregate into `apply_access_scope`; Python filter is fine + explicit.)
- Tests `tests/api/test_collection_perm.py`: private hidden from list; auto-CRUD
  `GET /collection/{id}` → 404 for non-owner; superuser sees all.

## What's now DONE (items 1, 2, 3, 5 — completed this session)

1. **Permission-set endpoint** `PUT /kb/collections/{id}/permission` — body =
   visibility + grant lists (full replace); gated with `authorize(...,
   "change_permission", ...)` (404 if you can't `read_meta` it, 403 if you can't
   change it); persists **as the owner** (`rm.using(created_by)`) so the write
   checker's `write_meta` gate doesn't block a `change_permission`-only delegate;
   emits a `Notification(kind="share")` to newly-granted users. `kb_routes.py`.
2. **Per-verb write checker** — `perm/checker.py::CollectionPermissionChecker`.
   `update`/`modify`/`patch` → `write_meta` (a `permission` change additionally
   needs `change_permission`); `delete`/`permanently_delete`/`switch`/`restore`
   → owner/superuser only. The FE edits via **PATCH** and deletes via
   **`DELETE …/permanently`** — both now gated.
   - **specstar 0.11.11 gotcha (important):** `add_model(permission_checker=…)`
     is SILENTLY SHADOWED — `ResourceManager` is built with
     `self.permission_checker or permission_checker` and the spec default is a
     truthy `AllowAll()`, so the per-model checker never runs (only `access_scope`
     is threaded straight through). We attach the checker via the per-model
     `event_handlers` slot instead (wrapping it in `PermissionEventHandler`). One
     consequence: it fires on EVERY `ResourceManager` write, not just
     request-routes — the lone programmatic Collection write (`code_repo` sync's
     git-metadata stamp) now writes **as the owner** to pass `write_meta`.
   - We do NOT use `ActionBasedPermissionChecker` (its `not_applicable` for
     unmapped actions is treated as a denial → would 403 reads/creates); the
     custom checker returns `allow` for everything outside the gated verbs.
3. **Content-route guards** (`kb_routes.py::_authorize_collection`): `POST
   .../documents` + `.../import` → `add_content`; `sync` / `reindex` →
   `edit_content`; `PUT .../wiki/page` → `edit_content`. `read_meta`-first (404,
   no existence leak) then the verb (403).
5. **`superusers` wiring**: `ServerSettings.superusers` →
   `factories.get_spec(make_spec(superusers=…))` AND `create_app(superusers=…)`
   → `register_kb_routes` (route-level `authorize`). Documented in
   `config.example.yaml`. Prod has no `config.yaml` (see
   `ai-workspace-prod-deployment` memory) so the set is empty until configured.

## What REMAINS (deferred to a follow-up)

4. **SourceDoc access inheritance** (deferred — design-uncertain, the handoff
   flagged it). A SourceDoc's access should = its parent collection's
   `Permission` (no per-doc perms in v1). **Residual gap until done:** the
   auto-CRUD `GET /source-doc/{id}` and the hand-written doc READ routes
   (`GET /kb/documents`, `list_documents`, chunks, export/download) do NOT yet
   inherit the collection's visibility — a non-member who knows/guesses a doc id
   could read a restricted collection's document. Collection-level visibility +
   all WRITE/content-mutation paths ARE enforced. Likely fix: a SourceDoc
   `access_scope` joining on `collection_id`, or guard the doc read routes the
   same way `_authorize_collection` guards the mutation routes (verb =
   `read_content`).

## Out of scope (later PRs / issues)
App-item enforcement (PR), KbChat migration to `Permission` (PR), background
workers + `use_terminal`, the owner tightening **UI** (PR 6), a first-class
logical `Group` entity + governance UI, per-doc perms. The `ask_knowledge_base`
/ KB-chat `collection_ids` transitive checks (B-2 in plan-permissions.md) ride
the same `authorize(read_content)` against the speaker.

## Resume
`cd` into the worktree (or `git checkout worktree-issue-262-collection-enforce`),
`uv sync --all-extras`, then `uv run pytest tests/api/test_collection_perm.py`.
Related: #242 (merged, speaker identity), #275 (lookup_user follow-up), specstar
#398–#401 (the access_scope feature).
