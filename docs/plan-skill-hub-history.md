# Skill hub:歷史與回溯、下載/使用次數、問 AI 該裝哪個

**狀態:** grill 進行中(2026-10-06 起)。下面「已定案」的每一條都問過、答過;「還沒定」的是我的建議,**不是**前提。
來源標記:〔user〕= user 的原話或明確選擇;〔查證〕= 讀程式碼確認的事實;〔建議〕= 我提的、尚未確認。

## 要做的三件事〔user〕

1. 記錄並能顯示歷史,**能回溯**。
2. 記錄每個 skill 總共被下載 / 使用幾次。
3. 可以問 AI「我該安裝哪個 skill」。

## 現況〔查證,master `24e951d8`〕

- 重新發布 = `SkillHubEntry` 的一個新 specstar revision;轉移 owner、改可見範圍也各是一個 revision,沒有被 prune。
  revision 帶 `updated_by` / 時間,所以「誰、何時做了什麼」的資料已經在,只是沒有 API、沒有畫面。
  fork 記在 fork 那一條的 `forked_from`。
- skill 的**檔案不在 revision 裡**:每版寫進 `create_app` 收到的 `filestore`(= `sandbox_filestore`,
  `factories.py:get_sandbox_filestore`)底下一個新的命名空間 `skill-hub:<id>:<uuid>`,`SkillHubEntry.blobs`
  記它在哪;發布完 `purge(previous)` 把上一版刪掉。所以舊版內容拿不回來。
  依 `sandbox.durable.kind`,這個位置可能是 specstar `WorkspaceFile`、NFS 檔案樹,或兩者雙讀。
- item 裡的 agent 已經有 `search_skill_hub` / `install_skill`,但只在 item 聊天裡;skill hub 頁面本身不能問 AI。
- 既有缺陷(不是這次造成的):`publish` 先 `find(owner, name)` 再 `mint_entry_id()`,同一個人同時發布兩次
  同名的**新** skill 會建出兩條同 `owner/name` 的條目。

## 已定案

### 功能 1:歷史與回溯 —— 儲存

| # | 決定 | 來源 |
|---|---|---|
| H1 | 不用 git,維持 specstar | 〔user〕「如果要維持 specstar…」「用 specstar 的 purge 就好」 |
| H2 | 保留上限預設 **N = 1000** 版,用 specstar `prune_revisions(keep_last_n=N)`;排序、目前版本永不刪、並發保護、blob 引用計數都交給它,沒人引用的 blob 由 blob GC 回收 | 〔user〕 |
| H3 | 檔案搬進 `SkillHubEntry` 本身:新欄位 `files: dict[str, Binary]`,每次發布就是一個 revision;內容相同的檔案在 revision 之間共用同一個 blob。不再經過 `sandbox_filestore`,也不再有每版一個命名空間 / 發布後 purge 那一套 | 〔user〕(「為什麼那麼複雜」之後同意的形狀) |

### H3 的相容做法〔user:「寫進計劃」〕

- **舊資料:** 新欄位有預設值,舊的列直接讀得出來(`files` 是空的),**不需要 Schema 升版,也不用跑 migrate**。
- **讀取:** `files` 有東西就用 `files`;是空的就照現在的方式從 `blobs` 指的 FileStore 位置讀。NFS、specstar、
  雙讀模式都走同一個 FileStore 介面,所以三種部署都相容。
- **寫入:** 新的發布一律寫進 `files`。舊條目只要重新發布一次,就自然轉成新格式。
- **歷史:** 改版之前的舊版本本來就被刪掉了,所以歷史從「改版後第一次發布」開始。舊格式的那一版在歷史裡還看得到,
  內容照樣從舊位置讀。
- **清理舊位置:** 條目被刪除時,連同舊位置一起清掉,跟現在一樣。舊格式的那一版被 `prune_revisions` 擠出 1000 版
  時也要一起清,但實際上不太可能發生。

`blobs` 欄位保留,只用來讀舊資料。之後若要完全拿掉雙讀,另做一次性搬移 job,不在這次範圍。

**推翻舊計畫:** `plan-skill-hub.md` 寫「payload 的每個檔案存成 blob(走既有 FileStore,一個 skill hub 命名空間),
不塞進 struct」,H3 推翻它。依 CLAUDE.md,實作的 PR 要在 `plan-skill-hub.md` 標題下加
`> 被 #<那個 PR>（plan-skill-hub-history.md）推翻`。

## 還沒定(建議,等 user 確認)

| # | 問題 | 建議 |
|---|---|---|
| O1 | 回溯怎麼做 | **加一版**:把 v2 的內容發布成 v4,歷史維持一條直線、不改寫;已安裝的人照常看到「有新版」。不用 specstar `switch()`——那會讓 v3 變旁支、prune 時優先被刪,而且已安裝副本看到的「有新版」其實是退回 〔建議〕 |
| O2 | 誰能回溯 | 只有 owner,和修改 / 下架 / 轉移一樣 〔建議〕 |
| O3 | 回溯要不要重新 AI 審查 | 不用:內容和當初審過的那一版完全相同,沿用那一版的審查結果 〔建議〕 |
| O4 | 其他人能不能直接裝舊版 | 第一版**不能**;能看舊版、比對兩版、從某一版 fork。理由:舊版常是有理由被換掉的(要做就得配「撤回某一版」);「有新版」提示會一直催故意用舊版的人(要做就得配「固定在這版」)。已安裝的副本本來就停在安裝時那一版 〔建議〕 |
| O5 | 事件歷史(轉移、下架、改可見範圍、被 fork)要不要和版本放在同一條時間軸 | 同一條,放 skill 詳情頁 〔建議〕 |
| O6 | 運營方要不要看得到 hub 總用量 | 要,方便發現濫用 〔建議〕 |
| — | 功能 2(下載 / 使用次數) | 還沒討論 |
| — | 功能 3(問 AI 該裝哪個) | 還沒討論 |
| — | 既有缺陷:同時首次發布同名 skill 產生兩條 | 還沒討論要不要順手修 |
