# Plan: Defect Library — 影像缺陷知識庫 + 分類輔助 (#513)

> **#513**:現場有成千上百種 defect type,判斷邏輯散在各站點 case-by-case 的程式
> (kernel,rule + ML)裡,沒有整理過的知識層、也沒有 AI 輔助工程師判斷「這張圖是哪一種
> defect」。延伸自 #104。

## ⚠️ 重新框定(2026-07,supersedes 先前所有 defect-specific 設計)

user 明確拍板:**不為 defect library 開發任何客製化功能**。原話——
「程式碼裡面不要有 defect / classify / tool 什麼什麼,他就是用 **context card 與 doc
question、card suggestions、擅用 multiple keys**」、「幾乎不該有新程式碼,我們需要**平台
擴充功能,但不是客製化**」。

**原則**:defect library **不是一組功能,而是既有通用原語的一種「用法」**。唯一可接受的
新程式碼是**通用平台擴充**(任何 collection / 任何 use case 都受惠),絕不是 defect 專屬。
所有「defect 味道」的東西都應該落在**資料(卡片內容)+ 慣例(怎麼下 key)+ prompting**,
而非程式碼。

**因此已撤除**(commit `25cd0ca1`):`kb/defect_library.py`、`lookup_defect` +
`classify_defect` 兩個 agent tool、它們在 3 個 kb preset 的掛載、只為 classify 加的 KB-chat
`vlm_describer` 接線。這些正是被拒絕的客製化。

**保留**(它們本來就是通用平台擴充,只是措辭泛化掉了 defect):P2 影像向量插座、P6 外部圖
抓取 ingest、P7 SourceDoc attachments、P8 附件 UI、P9 retriever 父文合併。

## 架構鐵則(user 拍板)

1. defect lib = 一個 **KB collection**,**非新 App、非新 resource、非新 defect 專屬工具**。
2. image-embedding 模型由**他隊**做;架構要**加法非取代**(他隊交付前後都能動)。
3. 盡量複用既有機制;**新程式碼只能是通用平台擴充**。

## 三個用例 → 各自對應的既有 / 通用能力

- **C 查代號 → 定義 / 判斷標準 / 範例圖**(純文字、不需圖):
  - entry = **`ContextCard`**;station/層級靠 **scope-qualified multi-key** 進 `keys`
    (list):`etchtool07|M4`(機台)/ `etch|M4`(站型)/ `M4`(全域)。`norm_keys` 派生。
  - 查詢走**既有 `lookup_glossary`**(確定性 exact-key 查 context card)或 `kb_search`(模糊)。
  - **override(機台蓋站型蓋全域)= agent 由具體 key 查到廣 key、第一命中勝** —— 這是
    **下 key 的慣例 + prompt/collection guidance**,不是 `resolve` 程式碼。
  - **零新程式碼**;交付 = 種子卡 + guidance(見 P11)。

- **B 上傳圖 → 這是哪一種 defect**:三條檢索路(user 拍板),查詢圖是**對話當下的暫時
  上傳圖、不是 KB 文件**(「那張圖不是文件」):
  1. **以圖搜圖**(image→image):等他隊 `ImageEmbedder` 交付 → **P4**。
  2. **圖轉文 + metadata → 搜文 → top-k → 綜合作答**:對話當下用 VLM 把上傳圖描述成查詢
     文字(+ user 給的站點/條件 metadata)→ `kb_search` 打 defect 卡 → 綜合 + 推薦。
  3. **圖轉文無 metadata → 走 2 的路**。
  - 路 2/3 的**唯一通用缺口**:KB chat 訊息目前**只有文字**,無法夾一張暫時圖給 agent 看
    → **P10**(通用多模態 chat 輸入;任何 KB chat 想「看這張圖問問題」都受惠)。

- **A 系統自動餵產線圖 + 現成 indices → 砍掉**:沒有 image_id、圖→run 反查不到、indices 只能
  user 手動給。零 measurement 整合。

## flywheel(庫怎麼長大)= 零新程式碼、複用既有且皆通用

- **cardgen(#506,已完整落地)**指向 defect collection → 起草 scope-qualified 卡 → reconcile
  (keep/update/new)→ **人核 commit → ContextCard**。閉環已存在,冷啟近乎免費。
- **#377 DocQuestion**:agent 沒把握就發問,term 答覆**直接落地成 ContextCard**(`land_term_answer`)。
- **不**新增 `DefectLabel` resource、**不**做 auto-classify 捕捉(那是客製)。

## 現況查證(為什麼加法可行)

- **collection 沒有 `kind` 欄**(`resources/kb.py`);「長出來的」慣例 ⇒ image 不必發明 discriminator。
- **`DocChunk` 早有 `embedding` + `embedding_alt` 兩個可空向量欄**;加第三個 `embedding_img`
  是純加法(P2 已做),與描述向量並存。
- **retriever 合流吃 N 條 arm**,`None`-gated ⇒ image arm 是純加法。
- **圖今天透過 VLM 描述被索引**(`parsers/vlm_image.py`);原圖 bytes 留 `SourceDoc.content`。
- **ContextCard lookup 是 collection-scoped、`norm_keys` 精準 element membership** ⇒ multi-key
  scope 用既有 `.contains` 就成立(無需 resolve 碼)。
- **`lookup_glossary` 是確定性 exact-key context-card 查詢**(無 LLM、無 retriever)⇒ 用例 C 的查詢腳。

## Phases(flat integer)

- **P1 · ~~entry 模型 + scope-chain lookup_defect~~ → 已撤除(`25cd0ca1`)**
  - 原本加了 `defect_library.resolve` + `lookup_defect` tool,屬 defect 客製,被 user 拒絕。
  - 用例 C 改為**用法慣例**:scope-qualified multi-key ContextCard + `lookup_glossary` + prompt。見 P11。

- **P2 · 影像向量插座(通用、空的、零行為變化)—— DONE**
  - `DocChunk.embedding_img`(可空 `Vector`)+ `kb/image_embedder.py`(`ImageEmbedder` protocol +
    stub)+ retriever text→image arm(`None`-gated)+ `create_app(kb_image_embedder=…)` 接線。
  - 通用:任何要以圖為第三訊號的 collection 都用得上。行為零變化。

- **P3 · ~~classify_defect 圖分類 tool~~ → 已撤除(`25cd0ca1`)**
  - 原本加了 `candidates_in_scope` + `build_classification_prompt` + `classify_defect` tool +
    KB-chat `vlm_describer` 接線,屬 defect 客製,被 user 拒絕。
  - 用例 B 改為**通用多模態 chat 輸入(P10)**+ 既有 `kb_search`;查詢圖是暫時 chat 附件、非文件。

- **P4 · image embedder 接上(gated on 他隊交付)—— 未做**
  - 實作 `ImageEmbedder`(他隊模型 adapter),`create_app` 從 `None` 換真的;ingest 多算圖向量填
    `embedding_img`;re-index 回填。第三 arm 自動亮 → 用例 B 路 1(以圖搜圖)。
  - ⚠️ generic CLIP 對 wafer/SEM 是 OOD,八成要他隊用累積標註圖 fine-tune;交付含準確率驗證。

- **P5 · flywheel —— 零新程式碼(複用既有,皆通用)**
  - = cardgen(#506,已存在)指向 defect collection + #377 DocQuestion。無新 resource、無捕捉客製。
  - 交付 = **設定 + guidance**:collection 開 `auto_digest`、卡提案人核長庫。屬 P11 的用法交付。

- **P6 · 外部圖抓取 ingest(通用)—— DONE**
  - HTML/MD 裡的 `<img src>` / `![](url)` 外部連結 → SSRF allowlist 過濾 → `IImageFetcher` 抓回
    → 存成獨立 image SourceDoc(bytes 留著,VLM 描述 + P4 可上向量)。沿用 archive 展開接縫。
  - `kb.image_fetch.{allowed_hosts,timeout}`(schema + loader + example);空 allowlist=OFF、加法不變。
  - 通用:任何「文字帶內部圖連結」的知識都受惠(非 defect 專屬)。

- **P7 · SourceDoc attachments(通用、BE)—— DONE**
  - 附件 = **有父連結的子 SourceDoc**(`parent_doc_id`,indexed;空=頂層),走一般 parser dispatch →
    可搜 / 可上向量,零 per-type 碼。path 慣例 `{父}/.att/{…}`(無 hash;撞名走一般 409)。
  - CRUD 全走既有 doc 機制:`move` 保 `parent_doc_id` + 鎖 `.att/` 前綴、刪父 cascade 刪附件、
    `list_documents` 每列回 `parent_doc_id`。Schema v8→v9 no-op reindex。
  - 通用:任何文件要掛子資源(圖 / PDF / CSV)都受惠。

- **P8 · 附件顯示 + CRUD(通用、FE)—— DONE**
  - `KbDocIde` 依 `parent_doc_id` 把附件從 tree 切掉;新元件 `AttachmentBar`(卡列名/型別/大小、
    ＋上傳、每卡改名/取代/刪除)接編輯頁下方;點卡開既有 `KbDocViewer` drawer(零型別特判)。

- **P9 · retriever attachment-aware 父文合併(通用)—— DONE**
  - `Retriever._augment_with_parents`:命中附件 chunk(語意薄)→ 依 `parent_doc_id` 額外帶回父文件
    全文 passage(繼承分數、去重;無附件時 byte-for-byte 不變)。反向(父→附圖)延後。

- **P10 · KB chat 暫時圖附件 → VLM 描述 → 搜(通用多模態 chat 輸入)—— DONE(用例 B 路 2/3)**
  - **通用擴充**:KB chat 訊息可夾帶**一張暫時上傳圖**(非 ingest、非 SourceDoc)→ 平台在對話當下
    用 VLM 把它描述成查詢文字 → 併入 user 的文字(站點/條件 metadata)→ `kb_search` → agent 綜合作答。
    任何 KB chat 都受惠,非 defect 專屬;是用例 B 路 2/3 的地基(路 1 = 以圖搜圖,等 P4)。
  - **收斂的設計決定**:(a) 圖用 base64 掛在 `_MsgBody.image`(暫時、選配;非獨立 upload/非 doc id);
    (b) VLM 描述在 **route 的 turn 前處理**(`send_message` 的 `_fold_image`)注入 query,**沿用 #106
    卡片 pre-scan 的「augment agent_content、不動已存訊息」樣式**,`ChatTurnEngine` 零改;(c) FE 複用
    #364 的 `extractClipboardFiles` 貼/拖解析,不重造。**選 (A) 用完即丟**(user 拍板「不小就即丟」):
    `KbMessage` 是 `KbChat` 的內嵌 sub-struct(無 id/無 blob),存圖要動 chat 資料模型 + 新 blob 層 +
    endpoint + FE 歷史渲染 → 不小;即丟則零 resource 改動。
  - **實作**:BE `_ImageInput` + `_MsgBody.image` + `_fold_image`(decode base64→magic re-sniff→
    `describer.describe` 走 `asyncio.to_thread`→併 caption;無 VLM/非圖/壞 base64 皆 400 fail-loud);
    把 `vlm_describer` 以**通用名義**接回 `register_kb_chat_routes`(= 25cd0ca1 砍的那段,這次正當理由是
    通用多模態)。FE `kbImage.ts`(`fileToImageInput`/`stagedImagePreview`)+ `KbImageInput` 型別 +
    `useKbChat.send(content, image?)`(允許 image-only)+ `KbChatPanel` 附圖 chip/paperclip/paste/drop。
  - commit:BE `33887142`、FE `3e6883b1`。測試:BE 5(描述+併查詢、即丟不存、無 VLM/非圖/壞 base64 400)、
    FE 22(helper/hook forwarding/image-only/composer attach→chip→forward→clear/remove);full web 1696 綠、
    KB-chat 58 綠、ty/typecheck 綠。
  - ⚠️ 交付前 B 準度:主要價值是 triage + 資料累積;細分近似缺陷靠 P4 圖向量。**DoD 待補**:live canned
    check(真 VLM 一張缺陷圖 → 描述 → 搜到對的卡)。

- **P11 · 種子卡 + guidance(用法交付,零程式碼)—— 要做**
  - **guidance**:一份說明「怎麼經營 defect library」——entry = ContextCard;key 用
    `machine|code` / `type|code` / `code` 三層 scope-qualified 慣例;body = morphology + 白話判斷標準;
    範例圖 = P7 附件;override = 下具體 key + 由具體查到廣(prompt/guidance,非碼);查詢走
    `lookup_glossary` / `kb_search`;圖問走 P10;長庫走 cardgen(#506)+ #377。
  - **落地形態(擇一,皆非程式碼)**:優先做 **collection `guidance`(#90)** —— 把上面慣例寫成
    collection 層 guidance 文字,steer KB agent 依慣例查/建卡;種子卡走既有 create/upsert 或 collection
    ZIP round-trip(#101)。**不**寫進 mkdocs nav 除非要一份對外文件(避免 --strict 負擔)。
  - Unit:無(純資料 / guidance);驗證 = live —— 建幾張跨 scope 卡、查 code 驗 override、上傳一張圖走 P10。

## 相依 / gating

- **P2 / P6 / P7 / P8 / P9 / P10 已完成**(皆通用平台擴充,保留)。P10 是唯一還要寫的程式碼,已交付。
- **P4** gated on 他隊交付 `ImageEmbedder`(用例 B 路 1)。
- **P5 / P11 零碼**:設定 + guidance + 種子卡(operational,交運維/user 用真資料落地)。
- measurement 整合已移除,無外部系統相依。

## 待決 / 風險

1. **P10 圖怎麼掛訊息 + VLM 描述在哪層**:grill/TDD 時定;優先複用 workspace chat 附圖接縫(#364)。
2. **機台→type→layer 拓撲**:先 user 提供 scope(下 key + prompt),拓撲有了再自動展開。
3. **圖向量領域適配**:他隊需用累積標註圖 fine-tune;ground truth 靠 flywheel 累積。
4. **交付前 B 準度**:主要價值是 triage + 資料累積;細分準度靠 P4。

## DoD 待補

- P2 / P6 缺 live check(真圖 / 真 VLM)+ full 100% gate。
- P10 完成後需 live canned check(真 VLM 一張缺陷圖 → 描述 → 搜到對的卡)。
- **push 需 user 授權**(本 session 曾明確拒絕 push);commit 全留 worktree branch。

Related: #104 #106 #133 #377 #355 #506 #364 #90 #101
