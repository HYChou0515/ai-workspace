# RCA 3.0 — Plan: 多人協作 + KB 引用分析 + KB 頁面改版

> 這份計畫涵蓋一批新功能(來源:`design_handoff_rca_3.0/design.md` + 使用者的
> 6 點需求),經 `/grill-me` 把決策樹走過一遍後定案。**這是可勾選的追蹤文件** —
> 每完成一階段就把對應的 `- [ ]` 打勾並 commit;全部勾完代表這批做完。
>
> 規格細節進 [contract.md](contract.md) / [architecture.md](architecture.md);
> 這裡記「要做什麼、為什麼、順序、進度」。

---

## 0 · 進度總覽

| 階段 | 內容 | 狀態 |
|---|---|---|
| **P1** | 地基:`Auth`/`UserDirectory` Protocol + `<UserChip>` + `Notification` + bell + status 通知 | ✅ 完成 |
| **P2** | `CitationEvent` + 三種 cited 聚合(point 1) | ☐ 未開始 |
| **P3** | chat 分享(唯讀)+ shared-with-me(point 2) | ☐ 未開始 |
| **P4** | mention(人 @ 人 + agent `mention_user` 工具)(point 6) | ☐ 未開始 |
| **P5** | FE KB 大改:collection/doc 頁 + home nav + 抽屜 manage/history(points 3/4/5) | ☐ 未開始 |
| **P6** | `root_path` 小修 | ☐ 未開始 |

每階段完成定義:`uv run ruff check && ruff format --check && ty check` 全清、後端
`coverage report` **100%**、FE `pnpm typecheck`+`vitest`+`build` 綠、commit 完成、本表打勾。

---

## 1 · 範圍

使用者的 6 點 + 為了讓它們「真的能動」而長出的地基:

1. **記錄 chunk / doc / collection 的被引用次數**(`doc ≠ sum(chunk)`,演算法見 §6)。
2. **chat 分享**:預設私人、可分享(唯讀)、從「Shared with me」看到;分享發通知。
3. **home 左側**加 Knowledge / Chat 導航。
4. **ask-agent 抽屜**:除了 manage(→ KB 頁)再加 history(→ chats 頁)。
5. **collection / document 頁大改**(含 cited 欄位、檔案優先預覽、chunks toggle)。
6. **investigation 內 `@user`**:純「叫人來看」,不觸發 agent;agent 也能主動 `@`。

地基(grill 過程長出來的):**身分**(公司已有 auth + user 目錄)、**通知模型**、
**rich-user 渲染**。

### 非目標(這批明確不做)
- 真正的 auth/SSO/登入畫面(我們接公司 middleware 的 `Auth` 接縫即可)。
- **collection** 分享 / 權限 / per-collection activity / team 概念。
- 完整通知頁(只做 bell 下拉)。
- report 版本通知、`assignment`/`agent_done`/`system` 通知 producer。
- 多人**同時與 agent 互動**的 chat(mention 是其基礎,但本批不做協作回合)。
- KB 文件 inline 編輯。

---

## 2 · 決策樹(grill 定案 — 「為什麼」)

- **身分**:公司已有 user object(`{id, name, section, email, photo_url}`)與 auth
  middleware(`Auth.get_user_id()`;HTTP 自動帶入,手動 `rm.create` 要自己帶)。
  我們**不擁有 User**,改用注入的 `UserDirectory` Protocol(mock 先行)。使用者只有
  幾百人 → 選人 UI **一次抓全部、前端過濾**,不做 server-side 搜尋。
  - ⚠️ ingest 的 `store`/`index` 已用 `asyncio.to_thread` 離開 request → user id 必須
    在 HTTP 邊界先抓、當參數傳進去(`ingestor.store(user=...)` 已支援)。
- **通知**:通用 `Notification` resource;**polling**(沿用 bell 的 `useQuery`+
  `refetchInterval`);bell 改成顯示**個人通知**(取代原本的全域 activity 流);本批接
  `mention`/`share`/`status` 三種 producer,其餘 kind 留位不接。
- **引用計數**:append-only `CitationEvent` log;**每個 `[n]`**:`doc+1`、`collection+1`、
  每個 `source_chunk_id` 各 `chunk+1` ⇒ 刻意 `doc ≠ sum(chunk)`。**KB chat 答案 + RCA
  `ask_knowledge_base` 兩條路都算**。re-index 後舊 `chunk_cited` 視為歷史(doc/collection
  id 穩定不受影響)。(完整演算法見 §6。)
- **chat 分享 = 唯讀**:被分享者能讀整串,**不能發言**(只有 owner 能送)。不動
  `KbMessage`、不碰 `ChatTurnEngine` 的「新訊息取消舊回合」併發語意。
- **mention = summon primitive,與 agent 解耦**:訊息含 `@user` → **一律不觸發 agent**,
  純粹發通知 + 在對話留一筆 `role="mention"`;人和 agent 都能用(agent 透過
  `mention_user` 工具)。
- **rich user 渲染**:凡是顯示裸 user id 之處 → 名字 + 頭像。
- **home nav 位置**:Knowledge/Chat 放在 `[All open…] → [Template]` 之下、`[Topics]` 之上,
  自成一個 caps 小組(理由:home 以 investigation 為中心,投資案 scope 留最上,KB 是側門,
  topic 過濾器留最下最貼近表格)。

---

## 3 · 新增資料模型(specstar resources)

```python
class Notification(Struct):          # → resource "notification"
    recipient: str                   # 收件人 user id
    kind: str                        # mention | share | status | (assignment|agent_done|system 留位)
    title: str
    body: str = ""
    link: str = ""                   # 點了跳哪:/investigations/{id} 或 /kb/chats/{id}
    actor: str | None = None         # 觸發者 user id;agent 觸發時為 None 或 "agent"
    read: bool = False
    created_at: int | None = None    # epoch ms

class CitationEvent(Struct):         # → resource "citation-event" (append-only)
    collection_id: str
    document_id: str                 # opaque SourceDoc id
    source_chunk_ids: list[str]      # 該 [n] 合併的原始 chunk
    origin_kind: str                 # "kb_chat" | "rca"
    origin_id: str                   # kb chat id 或 investigation id
    cited_by: str                    # 發問者 user id (Auth.get_user_id())
    marker: int                      # 答案裡的 [n]
    created_at: int | None = None
```

既有 resource 的小改:
- `KbChat`:加 `shared_with: list[str] = []`(owner = `created_by` meta)。
- `Message`(RCA Conversation):加 `mentions: list[str] = []`,並支援 `role="mention"`。

**不新增** `User`(走目錄)、不新增 collection 可見性欄位、不新增 team。

---

## 4 · Protocols(注入,mock 先行)

```python
class User(Struct):  # 值物件,非 resource;UserDirectory 回傳
    id: str
    name: str
    section: str = ""
    email: str = ""
    photo_url: str | None = None

class UserDirectory(Protocol):
    def get(self, user_id: str) -> User: ...
    def list(self) -> list[User]: ...           # 幾百人,FE 抓全份快取
    def current(self) -> User: ...              # = get(current_user_id())
```

- **current user id**:注入一個 `get_user_id: Callable[[], str]`(real 讀 middleware 的
  request-scoped `Auth`;mock/測試回傳可切換的固定 id)。`create_app` 收這兩個(像
  Sandbox/FileStore 一樣),並有 mock 預設。
- 測試:注入 mock `get_user_id`(可在請求間切換以模擬多使用者)+ mock `UserDirectory`
  (seed 幾個假 user)。

---

## 5 · 端點 / FE 元件清單(參照)

**新端點**
- `GET /me` → 目前 User 物件。
- `GET /users` → User[](目錄;FE 快取、前端過濾)。
- `GET /notifications` → 目前使用者的通知(最近 N,未讀優先)。
- `POST /notifications/read-all` → 全部標記已讀;`POST /notifications/{id}/read` → 單筆已讀。
- `POST /kb/chats/{id}/share { user_ids }`(owner-only)、`DELETE /kb/chats/{id}/share/{user_id}`。
- `POST /investigations/{id}/mentions { user_ids, note? }` → 留 `role="mention"` + 發通知,**不跑 agent**。
- cited 數掛在既有讀取端點:`GET /kb/collections`(每個 collection 加 `cited`)、
  `GET /kb/collections/{id}/documents`(每份加 `cited` + `chunks`)、doc 的 chunk 級
  cited 給 chunks 檢視(render 端點或新增 `GET /kb/documents/{id}/chunks`)。
- `GET /kb/chats` 改為**依目前使用者過濾**(owner 或 ∈ shared_with)。

**新 FE**
- `useUsers()`(TanStack 快取目錄)+ `<UserChip>`;`useCurrentUser` 改打 `GET /me`(rich)。
- 通知 bell 下拉 + `useNotifications`(poll)+ mark-all-read + 未讀紅點;點擊跳 `link`。
- HomeSidebar:`KNOWLEDGE BASE` 小組(Knowledge → collections、Chats → chats)。
- AskAgentDrawer:manage + history 兩入口。
- KB collection 列表頁改版(KPI + 篩選 + `cited N×` chip)。
- Collection 詳情整頁(KPI + Documents 表:Name/Uploaded-by/Updated/Size/**Chunks**/**Cited**)。
- Doc 預覽:檔案優先 + chunks 藏 toggle 後(顯示每個 chunk 的 cited)。
- Chats 頁:My / Shared-with-me 兩區 + 分享動作。
- Investigation agent chat:`@` 自動完成 composer + `role="mention"` 渲染 + summon UI。
- 凡顯示裸 user id 處(investigation owner/members、chat author、通知 actor、collection owner)
  → `<UserChip>`。

---

## 6 · 引用計數演算法(point 1,精確版)

**事件**:每一個被持久化的 `[n]` = 一個 `CitationEvent`,帶它 resolve 到的 passage
`(collection C, doc D, source_chunk_ids {c_i})`。passage 是 parent-doc merge 的產物,
故 `{c_i}` 常為多個、且 chunker 有 overlap 會相鄰重疊。

**計數規則(聚合查詢 over the log,不放計數器)**:
- `collection_cited[C] += 1`、`doc_cited[D] += 1`(一個引用 = 該 doc/collection 記一次,
  **與合併幾個 chunk 無關**)。
- `chunk_cited[c_i] += 1` for **每個** `source_chunk_id`。
- ⇒ `doc_cited[D] ≤ Σ chunk_cited(over D 的 chunk)`。**刻意不等於 sum**:overlap/merge
  造成的膨脹只反映在 chunk 空間;doc/collection 用事件數,乾淨。
- **去重**:不去重 —— 同答案內同 doc 兩個 `[n]` 記 +2(agent 真的引用兩次)。

**記錄時機 / 兩條路**:
- **KB chat**:答案持久化時(`parse_citations` 已在 `kb_chat_routes` 的 persist callback 跑)
  → 對每個 `Citation` 寫一筆 `CitationEvent`,`origin_kind="kb_chat"`、`origin_id=chat_id`、
  `cited_by=` chat owner(目前使用者)。
- **RCA `ask_knowledge_base`**:`answer_question` 目前算了 `cites` 只組 footer 就丟。
  → 加一個 `on_citations` 出口;`_ask_kb`(有 investigation + `Auth` user)收到後寫
  `CitationEvent`,`origin_kind="rca"`、`origin_id=investigation_id`。

**聚合**:幾百份文件、引用量不大 → 掃 log 在 Python 聚合即可(`chunk_cited` 需 unnest
`source_chunk_ids`)。之後要快取再加 denormalized 計數器。

**re-index**:doc 重傳會刪舊 `DocChunk`、chunk id 變 → 舊 `chunk_cited` 事件指向不存在的
chunk,視為**歷史**(只在當前 index 世代內有意義)。doc/collection id 穩定,不受影響。

---

## 7 · 分階段建置(每階段 TDD → gate → commit → 本表打勾)

### P1 · 地基:身分 + 通知  ✅
- [x] `User` 值物件 + `UserDirectory` Protocol;mock 實作 + seed 假 user。
- [x] 注入 `get_user_id` + `UserDirectory` 進 `create_app`(mock 預設;測試可切換 current user)。
- [x] `GET /me`、`GET /users`。
- [x] `Notification` resource + 註冊。
- [x] `GET /notifications`、`POST /notifications/read-all`、`POST /notifications/{id}/read`。
- [x] **status producer**:investigation 狀態翻轉 → 通知 owner + members(驗證通知管線端到端)。
- [x] FE:`useUsers()` + `<UserChip>`;`useCurrentUser` → `GET /me`;footer 等顯示處換成 `<UserChip>`
      (其餘裸 id 顯示處隨 P5 觸及時換)。
- [x] FE:通知 bell 下拉(`useNotifications` poll + mark-all-read + 未讀紅點 + 點擊跳 link),
      取代原本 bell 的 activity 流。

### P2 · 引用計數
- [ ] `CitationEvent` resource + 註冊。
- [ ] 記錄:KB chat persist(每個 `Citation` 一筆)。
- [ ] 記錄:`answer_question(on_citations=…)` + `_ask_kb` 寫 RCA 來源事件。
- [ ] 聚合 helper:`collection_cited` / `doc_cited` / `chunk_cited`。
- [ ] 端點掛 cited:`GET /kb/collections`、`GET /kb/collections/{id}/documents`、chunk 級 cited。

### P3 · chat 分享(唯讀)
- [ ] `KbChat.shared_with`;`GET /kb/chats` 依目前使用者過濾(owner 或 ∈ shared_with)。
- [ ] `POST /kb/chats/{id}/share`(owner-only)+ `DELETE …/share/{user_id}`。
- [ ] **share producer**:分享時對新收件人發 `share` 通知。
- [ ] 被分享者唯讀(只有 owner 能 `send_message`;後端擋非 owner 發言)。
- [ ] FE:Chats 頁 My / Shared-with-me 兩區 + 分享動作(user picker)。

### P4 · mention(人 + agent)
- [ ] `Message.mentions` + `role="mention"` 支援。
- [ ] `POST /investigations/{id}/mentions` → 留 mention entry + 發 `mention` 通知,**不跑 agent**。
- [ ] FE:含 `@user` 的訊息走 mentions 端點(不送 agent);composer `@` 自動完成;
      chat log 渲染 `role="mention"` 為人對人事件。
- [ ] agent `mention_user` 工具:`AgentToolContext.mention` hook(像 `ask_kb`)→ API 層
      建 mention entry + 發通知(`actor=agent`);加進 RCA toolset。

### P5 · FE KB 大改(points 3/4/5)
- [ ] HomeSidebar:`KNOWLEDGE BASE` 組(Knowledge / Chats),位置在 Template 下、Topics 上。
- [ ] AskAgentDrawer:manage(→ collections)+ history(→ chats)。
- [ ] Collection 列表頁改版:KPI(My collections / Most cited)+ 篩選 + `cited N×` chip。
- [ ] Collection 詳情整頁:KPI + Documents 表(Name/Uploaded-by/Updated/Size/Chunks/Cited)。
- [ ] Doc 預覽:檔案優先 + chunks toggle(每 chunk cited);沿用既有 kb:// 連結處理。
- [ ] 全頁接真資料:cited(P2)、owner=`created_by`、size=bytes 加總、chunks=`DocChunk` 數。

### P6 · root_path 小修
- [ ] `create_app(..., root_path="")` → `FastAPI(root_path=...)`;`__main__` 傳進去、
      `uvicorn.run` 拿掉;測試 `create_app(root_path="/x").root_path == "/x"`。

---

## 8 · 標準限制(沿用 CLAUDE.md)
- 回應繁中;code/識別字/commit 訊息英文。
- 後端:uv + pytest + ruff + ty + `coverage.py`(**非** pytest-cov),**100% coverage**。
- FE:vitest TDD;`pnpm typecheck`/`vitest`/`build` 綠。
- specstar 一律 fresh `SpecStar()` 實例;struct 欄位用 `dict[str, Any]`。
- 不 push 遠端(只在本機 commit);**不改 `design_handoff_rca_3.0/`**。
- SourceDoc id 是**不透明 handle,永不解析**;RCA + KB 回合共用 `ChatTurnEngine`。
