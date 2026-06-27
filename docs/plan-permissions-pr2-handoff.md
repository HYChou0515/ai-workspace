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

## What REMAINS (to finish PR 2 / "全部做完")

1. **Permission-set endpoint** `PUT /kb/collection/{id}/permission` (the FE/UI
   backend) — body = visibility + grant lists; gate with `authorize(...,
   "change_permission", ...)` (owner / superuser / granted); persist; emit a
   `Notification(kind="share", actor=get_user_id())` (audit, mirror
   `kb_chat_routes.share_chat`). **Without this nothing can be set to
   restricted/private via the API — the access_scope is currently dormant.**
2. **Per-verb write checker** (so a restricted collection's read-only members
   can't update/delete it; access_scope only blocks outsiders):
   `add_model(Collection, permission_checker=ActionBasedPermissionChecker.from_dict({...}))`.
   Each handler is a `CheckFunc(context) -> PermissionResult` decorated
   `@requires_resource_parts(ResourcePart.DATA, ResourcePart.META)` to read
   `context.current_resource.data` (`.permission`) + `.meta.created_by`. Mapping:
   - `update` / `patch` → `write_meta`, **but** if `context.data.permission !=
     current_resource.data.permission` → require `change_permission` (so the
     generic PUT can't rewire access control; the dedicated endpoint passes
     because its caller has `change_permission`).
   - `delete` / `permanently_delete` / `switch` / `restore` → owner **or**
     superuser only.
   - deny → 403. Build `Actor.human(context.user)`; thread superusers.
   - Imports: `from specstar.permission import ActionBasedPermissionChecker,
     PermissionResult, requires_resource_parts, ResourcePart`.
3. **Content-route guards** (hand-written in `kb_routes.py`): `POST
   .../documents` → `add_content`; `sync` → `edit_content`; `reindex` →
   `edit_content`; `PUT .../wiki/page` → `edit_content`. Load the collection's
   `permission` + `created_by`, `authorize`, 403/404. (Pattern like
   `_can_read_meta`.)
4. **SourceDoc inheritance** (deferred): a SourceDoc's access = its parent
   collection's `Permission` (no per-doc perms in v1). Likely its own
   `access_scope` joining on `collection_id`, or enforce at the doc routes.
5. **`Settings.server.superusers` wiring**: thread the configured superuser set
   into `make_spec(superusers=...)` from `factories.get_spec` / `create_app`
   (today only the test passes it; prod has none configured yet). Note prod has
   no `config.yaml` — see the `ai-workspace-prod-deployment` memory.

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
