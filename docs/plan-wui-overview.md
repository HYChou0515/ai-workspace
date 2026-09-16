# Plan — WUI overview: one page that lists every deployed WUI

## Problem

A WUI lives in the item that made it, and nothing outside that item knows it
exists. To open one today you enter the app, open the item, find the view file
in the file tree and click it — or you hold the `/w/{slug}/{itemId}/{path}`
link somebody copied out of the Deploy panel (PR #803). There is no place that
answers "which WUIs are there?": `git grep -n 'wui' web/src/pages` matches only
`WuiPage.tsx` (the reader page), `wui_routes.py` has one route (build), and the
platform destinations (`usePlatformDestinations.ts`) list Knowledge base /
Review / Diagnostics / My resources / Groups / Work calendar / Help — no pages.

The thesis of a WUI is that a domain person composes one and colleagues then
*use* it. A page nobody can find is a page nobody uses — the "merged but
invisible" shape again, one level up: the feature is visible to its author and
invisible to everybody the author built it for.

## Locked decisions (from /grill-me, 2026-09-16)

| Question | Decision |
|---|---|
| What is the overview? | **A product page**, not a document: one page in the app listing WUIs, each row opening the page. (`docs/wui.md` is the document; it already exists.) |
| Which WUIs appear? | **Only pages that have been Deployed.** Deploy is an explicit act, so the platform records one row at that moment. This replaces the alternative — indexing every `view: wui` file at write time — which needed a content-aware hook on every write door (`*.ai.yaml` is also every board / table / gantt view, so a filename index would have listed every PM item and forced a yaml read per row). |
| So what does Deploy mean now? | **Build, confirm the page opens, hand over the link — AND list it on the overview.** The address itself still needs no Deploy (`WuiPage` answers it whenever the item is readable); Deploy is what puts the page where people look. `docs/wui.md`'s "Deploy 不是部署" sentence must change. |
| When does a row leave? | (1) **Automatically** when the item is deleted or the viewer may no longer read it — the listing is permission-filtered, so this is free. (2) **Manually**: a **Remove** on the overview row. Placed on the overview, not the pane toolbar, because the case that most needs it — the folder was deleted — has no pane to press anything in. |
| Auto-remove when the view file is gone? | **No.** On this platform "the file does not exist" is also what a sandbox mid-restore answers; auto-unlisting on that would make pages blink out after every idle reap and need a fresh Deploy to come back. A dead row opens the reader's existing sentence ("This page has not been published yet — or it is still being restored") and a person presses Remove. |
| Where does it live? | **Global**, `/wui`, beside My resources in the platform destinations, labelled **WUI** (a proper noun in the UI, like everywhere else), **visible to everybody**. Rows grouped by app, newest Deploy first within a group. Not per-app: a reader looks for "that page", the app is a segment of its address; and a global page needs no per-app empty state. Visible to all rather than only to people with a deployed page: an entrance that appears and disappears is the confusing kind, and an empty state can say what a WUI is. |
| Who may Deploy / Remove? | **`edit_content` on the item** — the people who may change what the item holds may put it up and take it down. Build keeps its own `execute` gate, unchanged. Superusers pass, as everywhere. |
| Row title? | **Taken by the server from the view file at Deploy time** (`title:` in the yaml; the folder name when absent), never from the request body. Re-Deploy refreshes it. No "name your page" dialog: Deploy stays one press. |

## Design

### The record — `DeployedWui`

```python
class DeployedWui(Struct):
    slug: str          # the app; part of the address
    item_id: str
    path: str          # the view file, workspace-absolute ("/pages/report/page.ai.yaml")
    title: str         # from the yaml at Deploy time; folder name when absent
    deployed_by: str
    deployed_at: int   # ms, `timeutil.now_ms`
```

- **Resource id is deterministic**: `item_id` + `path`, slash-free the way
  `filestore/specstar_impl._fid` spells one. So a second Deploy of the same
  page is an **update of the same row** (title / by / at), never a duplicate,
  and Remove is a point delete. No CAS: last Deploy wins is the meaning.
- **Registered post-`spec.apply`**, exactly like `_ScheduleIndex` /
  `_SandboxActivity` / `_SandboxAddress` — platform bookkeeping that must not
  grow auto-CRUD routes (an authenticated caller must not be able to PUT
  themselves onto the overview).
- `indexed_fields=["item_id", "deployed_at"]`: `item_id` so an item's rows can
  be found (Remove, and any later per-item cleanup), `deployed_at` so the
  listing sorts in the store. A new model has no rows to backfill — one line in
  `docs/migrations.md` §5 says so, so the next reader does not go looking.
- Hard delete (`permanently_delete`) on Remove: a soft-deleted row still lists
  (`reference_specstar_indexed_queries`), and "removed from the overview" must
  mean gone from the listing.

### Routes (`api/wui_deploy.py` — the record, its store and its routes in one module, the `schedule_index.py` shape; `wui_routes.py` keeps build / tools / run)

**`POST /a/{slug}/items/{item_id}/wui/deploy`** — body `{ "path": str }`.

1. `locator.require_access(slug, item_id, "edit_content")`.
2. Validate the path: workspace-absolute, `.ai.yaml`, not inside an ignored
   dir (`should_ignore(path, DEFAULT_IGNORES)` — the same rule
   `is_schedule_file` applies, for the same reason: a vendored copy is not a
   declaration anybody made).
3. **Read the view file** through the item's `WorkspaceFiles` facade (the
   publisher just built the page, so the sandbox is warm and this is the same
   read the pane made) and `yaml.safe_load` it. `view != "wui"` → 400 with a
   sentence naming the kind found; unreadable / unparsable → 400 with the
   reason. A page the client says opened but the server cannot read is not
   listed — the row is the server's claim, so the server checks it.
4. Upsert the row; return it.

**`DELETE /a/{slug}/items/{item_id}/wui/deploy?path=`** —
`edit_content`; 204 whether or not a row existed (Remove pressed twice is not
an error). Hard delete.

**`GET /wui`** — the overview.

1. Read every row, newest first (`.sort("-deployed_at")`). Bounded by the
   number of deployed pages, the same shape as the schedule sweep's listing.
2. For each **distinct** `(slug, item_id)`: `locator.require_access(slug,
   item_id, "read_content")` inside `try/except HTTPException` → a refusal of
   any kind (404 unknown, 410 deleted, 403) drops every row of that item.
   `require_access` holds the facts for `access_window`, so an item with
   several pages costs one lookup. Then the same for `"edit_content"` → the
   row's `can_remove`, so the FE draws Remove only where the server would
   accept it (the FE gate is a courtesy; the DELETE re-checks).
3. Reply: `{ "pages": [ { slug, item_id, item_title, path, title, deployed_by,
   deployed_at, can_remove } ] }`. `item_title` via `locator.title_of`. The
   address is built by the FE, exactly as `WuiView` builds it today
   (`API_BASE` + `encodePath`) — one spelling, not two.

### Deploy's last step (`WuiView.runDeploy`)

Today the run ends at `setDeploy({ path: mine, at: next, state: "done" })`
right after the verify read succeeds (`WuiView.tsx:934`). One step is added
between the verify and "done":

```ts
try {
  await wuiApi.deploy(slug, fs.scopeId, mine);
} catch (err) {
  if (moved()) return;
  setDeploy({ path: mine, at: next, state: "failed", step: "list", why: messageOf(err) });
  return;
}
```

- **"✓ Deployed" appears only after the row is written.** A page that shows
  "Deployed" and is not on the overview would be the silent failure this whole
  feature exists to remove.
- The failure sentence: **"Deploy failed — the page could not be listed in
  WUI: …"** with the server's reason (a 403 reads as "not authorized to
  edit_content", which is the true reason; a 400 carries the kind found).
- The record is made **after** the verify, not before: a page the publisher's
  own read could not open must not be listed.
- The request carries the run's `AbortSignal` like the build does, so Cancel
  and leaving the page stop it (PR #773's lesson: an abandoned promise is not
  a cancelled request).

### The overview page (`web/src/pages/WuiOverviewPage.tsx`, route `/wui`)

- One `useQuery` (`qk.wuiOverview`) on `GET /wui`; Remove is a `useMutation`
  that invalidates it. Client module `web/src/api/wui.ts` in the
  `myResources.ts` shape (`WuiApi` type + `wuiApi` object; the mock in
  `mock.ts` answers the same contract).
- Grouped by app: heading = app name + `AppIcon` + `appTagPalette` tag, the
  same three the Launcher and My resources use; apps come from `useApps()`, a
  row whose app is not in that list (a deregistered app) falls under its slug.
- A row: **title** (link → the reader address, opens in a new tab — the reader
  page has no navigation back, by design), **item title** (link → `/a/{slug}/
  {item_id}`; the workspace has no deep link to a file, so this opens the item),
  **deployed by · when** (relative time like the rest of the shell), **Remove**
  when `can_remove`.
- Remove asks once via `useDialog().confirm` (the platform's dialog, not
  `window.confirm`): "Remove *{title}* from WUI? The page and its folder stay;
  only the listing goes." A mistaken press costs one Deploy to undo, and the
  row's title makes it clear which page is meant.
- Empty state (no rows the viewer may see): one sentence — "No WUI has been
  deployed yet." — and one more saying what a WUI is, with a link to `/help`.
  Nothing else: no CTA to "create one" (that happens in an item, with the
  agent).
- `usePlatformDestinations`: `{ to: "/wui", label: "WUI", icon: "external" }`
  after `/my-resources`, unconditional. `App.tsx`: the route inside
  `GlobalLayout` beside `/my-resources`.

### Permission

The overview never widens access. A row is shown only to a viewer who may
`read_content` the item — the same verb `WuiPage`'s file reads are gated on —
so what the overview lists is exactly what the viewer could open by address.
Superusers see all rows, as they may open all items.

Deploy and Remove: `edit_content`. Recorded here because it is a NEW gate: the
Deploy of a page with no build touched no server route at all before this
plan, so a reader with `read_content` alone could press it and be handed the
address. They still can be handed the address (it is a shortcut, not a grant);
they can no longer put a page on the overview.

## Deliberately not doing

- **No content-aware write index** ("list every `view: wui` file"). Rejected
  in the grill: every write door would need to read the file, and the schedule
  index's history (plan-wui.md, rounds 3–5) is a list of doors that were
  missed. Deploy is one door.
- **No auto-unlisting on a missing view file.** See the locked decision.
- **No pane-toolbar Undeploy.** One entrance for Remove, on the page where a
  dead row is noticed.
- **No search / filter / pagination.** The table is bounded by deployed pages;
  add when the number says so, not before.
- **No title dialog on Deploy.** The yaml's `title:` is the page's name
  everywhere else in the platform; Deploy stays one press.
- **No per-app section on the app home.** One page.
- **No "last built" column.** It would need a read of `dist/index.html` per
  row; `deployed_at` is what the publisher vouched for.

## Phases (one commit each)

1. **Record + routes** — `DeployedWui`, `register_deployed_wui`, the three
   routes (`api/wui_deploy.py`), wired in `create_app` beside the quota routes;
   migrations note.
2. **Deploy lists** — `wuiApi.deploy` / `remove` / `list` client (+ mock), the
   `runDeploy` step and its failure branch, the `list` step's sentence.
3. **Overview page** — `WuiOverviewPage`, route, destination, empty state,
   Remove with confirm.
4. **Docs** — `docs/wui.md` "發布（Deploy）" section: Deploy now lists the
   page; the overview and Remove; the "Deploy 不是部署" sentence replaced.
   `plan-wui-deploy.md` gains a pointer to this plan.

## Test plan (red first, targeted only)

Phase 1 (`tests/api/test_wui_deploy_routes.py`):

- `POST …/wui/deploy` by a user with `edit_content` on a `view: wui` file →
  200, row has the yaml's `title`, `deployed_by` = caller, `deployed_at` set.
- Same file, no `title:` → title is the folder name (`/pages/report/page.ai.yaml`
  → `report`); a root view file (`/page.ai.yaml`) → the file's stem.
- `view: board` → 400, body names `board`; a path not ending in `.ai.yaml` →
  400; a path under `node_modules/` → 400; an unreadable path → 400.
- `read_content`-only user → 403; unknown item → 404.
- Deploy twice → one row, the second `deployed_at` ≥ the first (positive
  control: two different paths → two rows).
- `DELETE` with `edit_content` → 204 and the row is gone from `GET /wui`;
  `DELETE` again → 204; `read_content`-only → 403.
- `GET /wui`: rows for an item the viewer may read appear; an item the viewer
  may not read (private, not granted) contributes nothing; a **deleted** item
  contributes nothing (the row still exists in the store — assert that, so the
  test proves the filter and not a cascade); superuser sees both.
  `can_remove` is true for an `edit_content` holder and false for a reader.
- Order: newest `deployed_at` first.

Phase 2 (`WuiView.test.tsx`):

- Deploy on a page with no build: the verify read succeeds → `POST …/wui/deploy`
  is called with the page's path → "✓ Deployed". Assert the POST happened
  **before** the "Deployed" text (order, not just presence).
- The POST rejects (403 with a detail) → no "Deployed" text; the sentence
  "Deploy failed — the page could not be listed in WUI: …" carries the detail.
  Positive control: the same fixture with the POST resolving shows "Deployed".
- The verify read fails → the POST is **never** made (spy at zero).
- Cancel during the POST → the request's signal is aborted; nothing settles.

Phase 3 (`WuiOverviewPage.test.tsx`, `usePlatformDestinations.test.ts`):

- Two apps, three rows → two group headings, rows under the right one, newest
  first within a group.
- The title link's `href` is `${API_BASE}/w/${slug}/${itemId}${encodePath(path)}`
  for a path with a nested CJK folder (the exact spelling `WuiView` uses).
- Remove is drawn only for `can_remove: true`; pressing it opens the confirm;
  confirming calls `DELETE` and the row is gone after refetch; cancelling calls
  nothing (both halves, per the modal rule).
- Empty state renders the two sentences and the help link; the group headings
  do not.
- The destination list contains `/wui` for a non-superuser with no groups.

Phase 4: `mkdocs build --strict` stays green (`plan-*` is `not_in_nav`; the
`wui.md` edit is a section).

## Verified ground truth (file pointers, origin/master `a4b890b7`)

- Deploy today: `web/src/renderers/wui/WuiView.tsx:835` `runDeploy` — manifest
  read → `runBuild({ reload: false })` → verify via `wuiDocQuery` fetched fresh
  → `:934` `setDeploy({ path: mine, at: next, state: "done" })`. No server
  call of its own; the only route it touches is the build's. The address:
  `:792` `${window.location.origin}${API_BASE}/w/${encodeURIComponent(slug)}/
  ${encodeURIComponent(fs.scopeId)}/${encodePath(path)}` (`API_BASE` from
  `api/http.ts`, `encodePath` from `api/refPath.ts:53`).
- The reader route: `web/src/App.tsx:46` `/w/:slug/:itemId/*` → `WuiPage`,
  outside `GlobalLayout`; `/my-resources` at `:68` inside it.
- Build gate: `src/workspace_app/api/wui_routes.py:257` and `:391` —
  `locator.require_access(slug, item_id, "execute")`. Registration:
  `register_wui_routes` at `:160` (takes `locator`, `get_user_id`, …).
- Verbs: `src/workspace_app/perm/model.py:33` — `read_content`,
  `edit_content`, `execute`, … Grant lists per verb, no roles.
- Access: `src/workspace_app/api/locator.py:216` `require_access(slug,
  item_id, verb)` — 404 on `read_meta`, then 403 on the verb; facts cached for
  `access_window`; superusers via `self._superusers` (`:249`). Pure decision:
  `api/item_authz.py:182` `check_access`; facts: `:118` `load_access_facts`.
  `locator.title_of(item_id)` at `:129`.
- Bookkeeping-model precedent: `src/workspace_app/api/schedule_index.py` —
  `_ScheduleIndex` registered post-`spec.apply` via `register_schedule_index`
  (`contextlib.suppress(ValueError)`), constructed in `app.py:1095`; the
  ignored-dir rule `should_ignore(path, DEFAULT_IGNORES)` at `:100`.
- Slash-free deterministic id: `src/workspace_app/filestore/specstar_impl.py:89`
  `_fid` (path `/` → U+2215).
- yaml: `pyyaml>=6.0.3` in `pyproject.toml:22`; used as `yaml.safe_load` in
  `src/workspace_app/entity/catalog.py:14`.
- Time: `src/workspace_app/api/timeutil.py:8` `now_ms`.
- Destinations: `web/src/hooks/usePlatformDestinations.ts:47–60` — the fixed
  list; `/my-resources` at `:51`. Icon names: `web/src/components/Icon.tsx:9–15`
  (`"external"` exists; no `"globe"` / `"window"`).
- Client-module shape: `web/src/api/myResources.ts` — `MyResourcesApi` type +
  `myResourcesApi` object, `apiFetch`, `httpErrorFrom` on non-2xx
  (`api/http.ts:91`). Query keys: `web/src/api/queryKeys.ts:25` `myResources`,
  `:61` `wuiDoc`.
- Page precedent: `web/src/pages/MyResourcesPage.tsx` — `useQuery` on
  `qk.myResources`, `useApps()` + `appTagPalette` + `AppIcon` for the app
  heading, `useDialog` for confirms, `useT` for strings.
- The workspace has **no** file deep link: `git grep -n 'useSearchParams'
  web/src/pages` matches only KB pages; `openFile.tsx` is a context, not a URL.
- Docs: `docs/wui.md:165` "### 發布（Deploy）：給別人一個網址"; `:7–9` the
  "Deploy 不是部署" parenthesis; `docs/migrations.md:114` §5 案例總表.
- `mkdocs.yml:134` — `not_in_nav` covers `/plan-*.md`.
