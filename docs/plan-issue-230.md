# Plan — 介紹/說明頁 + AI 問答 (#230)

> 需要有個介紹頁面專門放使用說明、release note 等等,也可以讓 AI 來回答使用者問題。

## 一句話

新增一個 **平台級 `/help` 頁**:把「使用說明 + release note」當成一個 **系統 KB collection** 的內容(開機從 repo 自動 seed、`admin`/superuser 才能改),頁面內嵌一個 **預先 scope 到此 collection 的 KB chat**(帶輕量 help 人格)來回答使用者問題。**最大化複用既有 KB(檢索 / 引用 / chat)機制**,幾乎不造新概念。

這是 MVP;後續 **#281**(讀程式碼的 AI 生成 wiki)會往同一個 collection 餵 source-code-derived wiki,屆時 source doc 是完整原始碼而非單一 `CHANGELOG.md`。本計畫刻意把 help collection 保持成「一個普通、內容來源可抽換的 KB collection」,不為 #281 預作多餘設計,也不擋它。

---

## Grill 鎖定的決策(逐題)

| # | 決策 | 取捨 / 理由 |
|---|---|---|
| 1 | **承載形式 = KB collection**(非新 App、非只擴充 onboarding 彈窗) | App 天生綁 item model + 每 item 一個 workspace,而 help 是沒有 item 的單一資訊面,硬塞假 item 語意彆扭;onboarding 彈窗容量太小。collection 直接複用檢索/引用/chat。 |
| 2 | **內容來源 = 開機從 repo seed 的系統 collection**;**只有 admin/superuser 能 edit** | 內容跟 code 進 git、隨 release 出、release note 天生版本控管;AI 直接 `kb_search` 它。權限用既有 #262 機制達成,**不寫新權限碼**。 |
| 3 | **Seed 語義 = repo 為唯一真相,每次開機 upsert** | Ingestor doc id 以 `(collection, path)` 為鍵、相同 bytes no-op、不同 bytes last-write-wins;UI 手改的會被下次開機覆蓋,要改走 repo。admin 的 edit 權限主要是「鎖定不讓一般人改」。 |
| 4 | **頁面 = 薄一層專屬 `/help` 頁**:內嵌 KB chat + 連到 collection 文件 | 文件本體用既有 `KbDocPage` 渲染、搜尋用既有 KB chat,不重做。既是「介紹頁」又最大複用。 |
| 5 | **AI 接法 = 純 KB chat,預先 scope 到 help collection + 輕量 help 人格 prompt** | 複用 `kb_chat` preset + `ChatTurnEngine` + `KbChatPanel`,幾乎零新後端;prompt addendum 給「平台使用助手」的聚焦語氣。 |
| 6 | **內容組織(現在)= `CHANGELOG.md`(單檔,release note)+ 既有使用說明(`user-guide.md`)** | 現在保持最小;release note 先用單一 `CHANGELOG.md`。**前向相容 #281**:之後改成讀 source code 生成 wiki。 |
| 7 | **進入點 = GlobalNav 常駐 Help 入口 + onboarding 彈窗加「看完整說明→」連到 /help + Launcher 入口卡** | 到處可發現;現有首訪自動彈窗(#161)保留作簡短教學,彈窗內加連結到完整說明。 |
| 8 | **可見性 = 照常顯示為一個公開唯讀 collection**(命名如「Platform Help / 使用說明」) | 不過濾、最簡單;power user 也能在 KB 介面直接瀏覽其文件/wiki;與 #281 生成的 wiki 會出現在此處相容。 |

---

## 架構(複用點)

```
repo curated 內容(CHANGELOG.md + user-guide.md)
        │  開機 lifespan 內 idempotent upsert(ingestor.ingest, user=system)
        ▼
系統 KB Collection「Platform Help」
  · Permission(visibility="public", 所有 write 清單留空)
      → 公開可讀可搜;只有 owner(system user)+ superuser 能改
  · 照常出現在 /kb/collections(公開唯讀)
        │
        ├──► /help 頁(新 route,GlobalNav 常駐入口 + Launcher 卡 + onboarding 連結)
        │     ├─ 介紹文字
        │     ├─ 內嵌 KbChatPanel(collectionIds 鎖定此 collection、隱藏 picker、help 人格 prompt)
        │     └─ 重點文件清單 → 既有 KbDocPage 渲染(release note / 使用說明)
        │
        └──► (#281 後續) source-code → 生成 wiki 餵進同一 collection
```

### 關鍵實作座標(已驗證)

- **Boot seed 掛點**:`src/workspace_app/api/app.py` 的 `lifespan`(~885–939),接在 `_ensure_insights_collection`(581–594,既有「開機 by-name idempotent 建 collection」先例)之後;該 scope 內 `ingestor`、`spec`、`get_user_id`、`settings` 均可用。
- **建 collection + 餵檔**:`rm.create(Collection(name=..., permission=...))` → `ingestor.ingest(collection_id=cid, user=system_user, filename=path, data=bytes)`(回傳 SourceDoc id list)。寫入用 `rm.using(user=system_user)` 蓋 `created_by`。
- **權限**:`from workspace_app.perm.model import Permission`;`Permission(visibility="public")`(write 清單預設空)= 公開讀 + 只有 owner/superuser 寫。**注意**:`permission=None` 是 back-compat 的「公開且可寫」,鎖不住,必須給明確 `Permission`。
- **Settings**:`settings.server.default_user`(seed 用的 system user)、`settings.server.superusers`(= admin)。
- **AI**:複用 `kb_chat` preset + `KbChatPanel`;`useKbChat({collectionIds})` 已支援固定 collection、首訊自動建 `KbChat`。help 人格 = 在 KB chat system prompt 後面 append 一段(沿用既有 per-collection guidance/append 模式)。
- **KbChatPanel(FE)**:目前無 `collectionIds`/`hideCollectionPicker` prop(picker 只在 `chatId==null && empty` 顯示);需新增此二 prop 並把固定 `collectionIds` 餵給 `useKbChat`、`showPicker` 加 `!hideCollectionPicker`。

---

## 分期(flat integer;每期走 /tdd:red → green → refactor)

> 後端 100% coverage gate、FE vitest。先排能獨立驗證的最小切片。

### Phase 1 — 系統 help collection 的 boot seed(後端)
- 新增 `seed_help_collection(spec, ingestor, *, system_user)`:idempotent 確保「Platform Help」collection 存在(by name,仿 `_ensure_insights_collection`)、帶 `Permission(visibility="public")`、把 curated 檔案列表(初始:`CHANGELOG.md`、`docs/user-guide.md`)以 `system_user` upsert 進去。
- 在 `lifespan` 裡呼叫(以 `asyncio.to_thread` 包,避免 blocking I/O 在 event loop)。
- 新增 repo 根的 `CHANGELOG.md`(初始一筆,作 release note ref)。
- curated 來源用「程式內明確檔案清單」,不搬移既有 docs(避免動到 `docs/README.md` 連結)。
- **測試**:boot 後 collection 存在且唯一(重跑不重複建)、文件已 ingest、相同內容重跑 no-op、Permission 鎖定(非 owner/superuser 改→403、任何人讀/搜→OK)。

### Phase 2 — help collection 的取得 API(後端)
- 新增輕量端點(typed pydantic 回應)讓 FE 拿到 help collection 的 id + 重點文件清單(供 `/help` 頁鎖定 chat、列文件連結)。例:`GET /help`→`{ collection_id, documents: [{id, title, kind}] }`(kind 區分 release-note / guide,供前端分區)。
- 避免 FE 去 by-name 猜 collection。
- **測試**:回傳 id 正確、文件清單含 seed 的檔、collection 不存在時的行為(明確錯誤)。

### Phase 3 — KbChatPanel 支援鎖定 collection(FE)
- `KbChatPanel` 加 `collectionIds?: string[]` + `hideCollectionPicker?: boolean`;固定 `collectionIds` 餵 `useKbChat`,`showPicker = !hideCollectionPicker && chatId==null && empty && ...`。
- 不破壞既有 KB chat 用法(兩 prop 皆 optional,預設維持現行行為)。
- **測試(vitest)**:給 `collectionIds`+`hideCollectionPicker` 時不顯示 picker、首訊用該 collection 建 chat;不給時行為不變。

### Phase 4 — `/help` 頁(FE)
- 新 route `/help`(掛在 `GlobalLayout` 下):介紹文字 + 內嵌 `KbChatPanel`(鎖定 help collection、help 人格)+ 重點文件清單(連到既有 `KbDocPage`)。
- 文件本體不重做渲染;清單依 Phase 2 的 `kind` 分「使用說明 / Release notes」兩區。
- i18n 走既有 `useT`;若無 embedder/KB,文件仍可讀(渲染不需 embedding),只有 AI 問答需要 embedder(於 UI 上明確標示)。
- **測試(vitest)**:頁面渲染、chat 鎖定到 help collection、文件連結正確、空/錯誤狀態。

### Phase 5 — 進入點 & onboarding 串接(FE)
- GlobalNav 加常駐 **Help** 入口 → `/help`(與既有 onboarding「?」並存:`?` 仍是首訪簡短教學)。
- `OnboardingModal` 加「看完整說明 →」連到 `/help`。
- Launcher 加一張 Help 入口卡。
- **測試(vitest)**:Help 入口可點到 `/help`、onboarding 彈窗內連結存在且導向正確、Launcher 卡渲染。

### Phase 6 — 收尾(文件)
- 文件:`docs/development.md` §10 補一段說明此機制(seed 來源、如何更新 release note = 改 `help_content/CHANGELOG.md`、權限、前向相容 #281)。
- **help 人格 prompt — DEFERRED**:原想在 help chat 的 KB system prompt 後 append 輕量人格,但**現況沒有 per-collection 的 chat-prompt 追加掛點**(KB agent prompt 來自 AgentConfig;`*_guidance` 只作用於 wiki)。硬加會違反「不新增 preset / 輕量」決策,屬 scope creep。預設 KB agent prompt 對「知識庫即 help 內容」已足以帶引用回答 how-to,故本期沿用預設,人格列為後續 follow-up。
- **Live check**(LLM 功能 DoD):用 Ollama 本地小模型實測 `/help` 問答能正確引用使用說明/release note(replay 不足以證明)。**AFK 環境無保證的 Ollama,列為合併後的人工 follow-up**;CI(unit-only)不涵蓋 live LLM。

---

## DoD

- [ ] 開機自動建好「Platform Help」collection 並 seed `CHANGELOG.md` + `user-guide.md`;重跑 idempotent。
- [ ] collection 公開可讀可搜、非 admin 改會 403。
- [ ] `/help` 頁可從 GlobalNav / Launcher / onboarding 連結進入,內嵌 chat 鎖定到 help collection 並能引用文件回答。
- [ ] 重點文件清單可點開既有 `KbDocPage` 閱讀。
- [ ] 後端全測 + 100% coverage gate 綠;FE vitest + tsc + build 綠;`ruff` + `ty`(unscoped)綠。
- [ ] Ollama live check:問「怎麼用 X / 最近更新了什麼」能給帶引用的正確答案。

---

## 明確的非目標 / 前向相容

- **不做** per-release 多檔的 release-note 系統(現在單一 `CHANGELOG.md`)。
- **不做** 讀 source code 生成 wiki —— 那是 **#281**;本計畫只保證 help collection 是「普通、可被 #281 餵內容」的 collection。
- **不寫** 新權限模型 —— 完全用 #262 既有 `Permission` + superuser。
- **不搬移** 既有 `docs/*.md`(避免斷連結);seed 來源是程式內明確檔案清單。
