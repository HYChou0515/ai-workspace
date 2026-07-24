# Plan — Set permissions BY GROUP (org-canonical groups)

承接 **#608**（群組授權前端全盲）。把「分享權限只能逐一加 user id」補成「可以授權給一個 group」，並讓被 group 授權的人真的看得到、用得了他的權限。

---

## Goal

一個使用者要能在分享 collection / 文件 / work item 時，**授權給一個群組**（例如「Engineering」），而不是把 12 個人的 id 一個個貼上去。被授權群組的成員打開資源時，前端 affordance 要正確顯示（不能伺服器放行、畫面卻把按鈕藏起來）。

## Current state（已查證）

- **後端引擎已完成（#307 CLOSED）**：`group:<id>` grant 寫進任何資源的 `Permission`，`authorize()` 會解析成成員。
  - `src/workspace_app/resources/groups.py` — `Group{name, description, members}` + `groups_of(spec, user)`（indexed `members.contains(user)`）。
  - `src/workspace_app/perm/authorize.py` — `Actor.groups`（:34）、`.subjects` 把 group 攤成 `group:<id>`（:56-59）、`_granted`（:69-71）。
  - `src/workspace_app/perm/scope.py` — `subjects_of`（:33）、`GroupsProvider`（:28）、`_visibility_scope`（:66）；已接入 collection / source_doc / graph_evidence / conversation 的 access_scope。
  - 建帶 group 的 Actor 的呼叫點：`item_routes.py:314`、`kb_routes.py:764`、`kb_chat_routes.py:822` 等（全 `Actor.human(me, groups=groups_of(spec, me))`）。
- **後端 group 管理路由存在**：`src/workspace_app/api/group_routes.py`（create/list/get/add-members/remove-member/delete），但 owner=creator、`POST /groups` 人人可建。
- **前端完全零 group**：無 `/groups` 呼叫、無管理 UI、分享對話框只認 `user:`/`all`（`web/src/lib/permission.ts`、`itemPermission.ts` 忽略 `group:` subject），`/me` 不回 group membership。

## Non-goals（本次不做）

- **KB chat 的 group 分享** — KB chat 目前只有唯讀分享 popover、沒有完整 ACL 對話框；先做 **#609**（chat 完整權限 UI）再談。
- **SSO / IdP 群組同步** — v1 成員純手動維護（#307 已鎖定的設計）。`User.section`（公司部門）不是 perm 的 group。
- **巢狀 group** — 扁平（#307 已鎖定）。

---

## Locked design（來自 /grill-me）

### 心智模型
Org-canonical group：admin 治理的**全公司共用**團隊，**任何人分享時都能授權給任何 group**。不是個人化的 ad-hoc 小圈子。

### 角色能力
| 動作 | superuser | owner（單一，預設=creator）| maintainer（名單）|
|---|---|---|---|
| 建群 | ✓ | ✗ | ✗ |
| 指定 / 轉移 owner | ✓ | ✓ | ✗ |
| 加 / 移 maintainer | ✓ | ✓ | ✗ |
| 加 / 移 member | ✓ | ✓ | ✓ |
| 刪群 | ✓ | ✓ | ✗ |

- **owner** 最多一個。`owner` 欄位為 `None` ⇒ `created_by`（建立者）就是 owner。
- **maintainer** 是名單，只能管成員，**不能再往下委派**（避免管理權無限擴散）。

### 分享 picker 的隱私
picker 對一般使用者只顯示 **群名 + 成員人數 + description**，**不展開成員名單**。完整名單只給該群的 owner/maintainer/member 與 superuser 在 `/groups` 管理頁看。

### 管理 UI
專屬 `/groups` 頁 + 導覽入口（對 superuser + owner/maintainer 可見；一般使用者看不到此頁，只在分享 picker 碰到群）。

### 端到端範圍
設定（picker 選群）**＋** 顯示/移除既有 group grant **＋** 閘門（`/me` 回 group、FE 閘門 helper 認得 `group:`）。三塊一起做。

### 小決定（架構上無實質選擇，採預設）
- **刪群後既有 `group:<id>` grant** → 靜默失效（解析出 0 成員）。分享對話框把無法解析的 group 顯示成「Unknown group」讓 owner 清掉。（擋刪除／連動清除需要「反查哪些資源引用此群」的反向索引，現在沒有。）
- **群名唯一性** → 不強制；name 自由文字、grant 綁 `resource_id`、UI 顯示 name。重名不會壞。
- **`/me` group 快取** → `staleTime: Infinity`；成員變動後畫面 affordance 需重整頁面才更新。v1 可接受。

---

## Phases（flat integer；逐 phase 走 /tdd + commit）

### P1 — Group model 加 owner + maintainers
- `resources/groups.py`：`Group` 加 `owner: str | None = None`、`maintainers: list[str] = []`。
- 有效 owner = `group.owner or created_by`（抽一個純函式 `effective_owner(group, created_by)`）。
- 舊資料：msgspec 預設值處理缺欄位（owner=None→created_by 是 owner、maintainers 空）——**無需資料遷移**（#262 保留 namespace 的用意）。
- ⚠️ **specstar 索引**：`list_groups` 要能查「我 own / maintain / member / created」——`owner`、`maintainers` 需進 `indexed_fields`（`members` 已索引）。舊 row 缺 `owner`（None），但它們由 `created_by`（meta，已索引）覆蓋到，故**不需 `rm.migrate` backfill**；查詢是 `created_by==me OR owner==me OR maintainers.contains(me) OR members.contains(me)`。
- **驗收**：`effective_owner` 純函式測試；舊 Group row 讀出來 owner 落到 created_by。

### P2 — group_routes：治理權限收斂 + 委派 + 轉移
- `POST /groups`：**鎖 superuser**；body 可帶 `owner`（指定初始 owner，用 `rm.using(owner)` 讓 `created_by`=指定人，或存進 `owner` 欄位）。
- 新端點：`POST /groups/{id}/maintainers` / `DELETE /groups/{id}/maintainers/{user}`（owner 或 superuser）。
- 新端點：`PUT /groups/{id}/owner`（轉移；owner 或 superuser）。
- `DELETE /groups/{id}`：owner 或 superuser（收緊：現在是 `_require_owner` 已含 superuser，維持）。
- 閘分層：`_require_manager`（owner ∪ maintainers ∪ superuser）管**成員**；`_require_owner`（有效 owner ∪ superuser）管 **maintainers / 轉移 / 刪群**。
- **驗收**（先紅）：一般 user 建群→403；owner 加 maintainer→204，maintainer 加 member→204，maintainer 加 maintainer→403；轉移後舊 owner 失去管理權；每條含「無權被拒」測試。

### P3 — pickable 端點 + /me 回 group + work-item scope 補洞
- 新端點 `GET /groups/pickable`（暫名）→ `[{id, name, description, member_count}]` 給**所有登入者**（不含 member id，無隱私外洩）。
- `GET /groups`（管理頁用）→ 回「我 own/maintain/member/created 的群」＋每群我的角色（owner/maintainer/member），供 UI 決定可否編。
- `api/meta_routes.py` `GET /me`：加 `groups: [id...]`（`groups_of(me)`）。同步 `web/src/api/real.ts` getMe 型別。
- `perm/scope.py:315-323` `work_item_access_scope`：委派 `collection_access_scope` 時**傳入 `groups_provider`**（目前漏傳，導致 work item 的 storage-layer list scope 不解析 group grant）。
- **驗收**：pickable 端點回名+人數、不回 member id；`/me` 帶 groups；透過 group 授 read 的使用者，work item list scope 查得到（先紅）。

### P4 — FE `/groups` 管理頁 + 導覽
- 新 route `/groups`（`web/src/App.tsx`）+ 導覽入口（對 superuser + owner/maintainer 可見）。
- 列出群（名/描述/owner/人數/我的角色）；`[+ New group]`（superuser）→ 建群 + 指定 owner；每群 `[Edit]` → 編成員（owner/maintainer）、管 maintainers + 轉移 + 刪群（owner/superuser）。
- 新 api client 方法（`web/src/api/*`）+ TanStack Query keys；走 `web/src/hooks/useIsSuperuser` + `/groups` 回的角色決定按鈕。
- **驗收**：superuser 見建群鈕、owner 見自己群的 Edit、maintainer 只見成員編輯、一般 user 無此頁（FE 測試）。

### P5 — FE 分享對話框：group picker + 顯示/移除既有 group grant
- `web/src/lib/permission.ts` + `itemPermission.ts`：`grantsFromPermission` / `permissionFromGrants` 認得 `group:<id>` subject（目前忽略/原樣保留）——decode 成可顯示的 group grant 列、encode 寫回。
- `components/PermissionDialog.tsx`（collection + per-doc）、`components/ItemShareDialog.tsx`（work item）：加 group picker（讀 `GET /groups/pickable`，顯示名+人數），既有 group grant 顯示成「群名 — 角色」列可移除；解析不到的顯示「Unknown group」。
- **驗收**：選群→PUT 寫進 `group:<id>`；開既有 grant→看得到群列並可移除；刪除的群顯示 Unknown group（FE 測試）。

### P6 — FE 閘門：helper 認得 group
- `web/src/lib/itemPermission.ts`：`hasItemVerb` / `canWriteItem` / `canChangeItemPermission` 吃呼叫者的 `groups` 集合，`group:<id>` ∈ my groups 也算命中；`web/src/lib/permission.ts` 的 `canManageAccess` 同理（若 collection change_permission 委任經 group）。
- `web/src/hooks/useCurrentUser` / `useItemAccess`：把 `/me` 的 groups 帶進閘門（比照 #580 的 `isSuperuser` 必填參數手法，讓 call site 不能漏）。
- **驗收**：透過 group 授 edit 的成員，`useItemAccess.canWrite`=true、看得到編輯/Save/composer（FE 測試，含「非成員仍被擋」負向鎖）。

---

## Acceptance（整體）
- alice(owner) 在 `/groups` 管 Engineering 成員、加 dave 當 maintainer、可轉移。
- erin（非成員）分享她的 collection → picker 選「Engineering · 12 人」→ 寫入 `group:eng`。
- bob（Engineering 成員）打開 erin 的 collection → 看得到並能用其權限。
- erin 重開分享 → 見「Engineering — Viewer」可移除。
- 後端 100% 覆蓋；FE vitest 綠；`ruff`/`ty` 乾淨；全套 gate 走 CI。

## 相依 / 後續
- **#609** — KB chat 完整 ACL UI（做完才把 chat 納入 group 分享）。
- **#610** — admin 的 AI 不繼承 superuser（獨立，與本案無關）。
- 未來：SSO/IdP 群組同步、巢狀 group（namespace 已保留、免遷移）。
