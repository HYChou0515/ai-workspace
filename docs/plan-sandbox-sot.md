# Plan — Sandbox as the single source of truth for files

> 來源:使用者回報「sandbox 與 filestore 雙真實來源 → sync 複雜、路徑不一致、LLM 看到的(FileStore)與實際的(Sandbox)有落差」。經 `/grill-me` 把決策樹走過一遍後定案(Q1–Q8)。
> **這是可勾選的追蹤文件** — 每完成一階段就把對應的 `- [ ]` 打勾並 commit;全部勾完代表這批做完。
> 制定日期:2026-05-26。

## 0 · 進度表

| 階段 | 內容 | 狀態 |
|---|---|---|
| **P1** | 地基:`WorkspaceFiles` facade + Sandbox Protocol 補檔案操作 + `version`;facade 暫背 FileStore(行為不變) | ✅ 完成 |
| **P2** | facade 改「熱→sandbox、冷→快照」路由;`exec`-only 喚醒;agent 檔案工具吃 facade(**落差問題在此解決**) | ✅ 完成 |
| **P3** | 鏡像改 PULL(`walk`+`version`、含刪除)+ ≤5s 節流 + refresh/turn-end flush;砍掉舊的 `dirty`/`flush` | ✅ 完成 |
| **P4** | `edit_file` + `write_file` CAS(內容式;衝突回現況);human last-writer-wins | ✅ 完成 |

延後項:**bulk archive 傳輸**(`upload_archive`/`download_archive`)記在 GitHub **issue #12**,實測會痛再做。

---

## 1 · 核心模型

**唯一真實來源(SoT)= 「sandbox 活著就是 sandbox,沒活著就是快照(FileStore)」。**

- 讀 / 寫永遠打「當下的 SoT」。
- **只有 `exec`(或任何需要活著的行程的東西)會喚醒** sandbox = `create` + `restore`(快照→sandbox);之後 SoT 變成 sandbox。
- **閒置回收 / 關閉** = 最後鏡像一次 + `kill`;SoT 換回快照。

任何時刻只有**一個**會被寫入的來源,中間沒有「兩邊同時可寫」的空窗 → 落差問題從根本消失。因為 agent 一旦要 `exec` 就一定是熱的,**agent 看到的永遠是 sandbox**。人類 UI 讀的是快照(便宜、最多落後 agent 5s、會收斂)。

---

## 2 · 定案決策(含被否決的替代方案)

1. **存活模型(Q1)= 隨需喚醒**,不是「打開 investigation 就建」。冷 investigation 免費;第一次互動暖起來。
2. **喚醒觸發點(Q1b/Q2)= 只有 `exec`。** 讀/寫本身不喚醒——冷的 sandbox 是凍結的,快照就是它的精確影像,讀它不會誤導;寫進當下 SoT 即可。
3. **鏡像時機(Q2)= 節流的「每次變更就鏡像」。**
   - agent 寫 → 節流 **≤5s、合併**(省 overhead;崩潰最多丟最近 ≤5s)。
   - 人類寫 → **write-through 立即鏡像**(人類寫很少,且要 read-your-writes)。
   - **refresh 按鈕 / turn 結束 / 閒置 / 關閉** → 強制 flush。
   - 否決:turn 邊界才鏡像(review 不夠即時)、只在 teardown 鏡像(崩潰丟整個 session)。
4. **變更偵測(Q3b)= `FileEntry.version: str`(不透明,各 backend 自己算)。** mirror 持有 `{path: 上次鏡像 version}` 自己 diff;listing 不見的 path = 刪除。
   - backend 實作:Local→mtime(+size,避免同秒撞)或 hash;Docker→`find -newer`/stat;Mock→遞增計數器;爛 backend→content hash(正確但不便宜)或哨兵(強制全抄)。
   - 否決:`changed_since(token)` 由 backend 記狀態(Mock 變複雜;有 inotify 的 backend 將來再加 fast-path)。
5. **Sandbox Protocol(Q4)= 擴充結構化檔案操作**(`delete`/`exists`/`mkdir`/`rmdir`/`rename`),而非在 facade 用 `exec` 拼(避免 shell quoting/binary 雷、避免每個操作一次 exec)。
6. **FileStore(Q4)= 維持 per-path,降為鏡像目標 + restore 來源 + 冷讀來源 + 人類 UI 讀來源。** `dirty_paths`/`clear_dirty` 整套刪除。否決:收成單一 tar/zip blob(人類讀檔/列樹要解壓,痛)。
7. **並行(Q6)= 人類 last-writer-wins、不加鎖。** FS 本來就能處理覆蓋;UI 顯示「agent 執行中」。
8. **agent 寫入安全(Q7)= 每次寫都帶 expected。** `edit_file(old_string,new_string)`(逐字 expected,最防閉眼寫)+ `write_file(path,content,expected_version)`(建新檔=expected 不存在 / 整檔重寫=expected version)。**CAS 失敗回「現況內容 + 新 version」**,讓 agent 一個 round-trip 重寫。人類寫不帶 expected(所見即所得)。agent 的 CAS 同時接住人類的並行編輯。`exec` 內 shell 自己寫的檔 facade 看不到,先天接不住(可接受)。

---

## 3 · 檔案存取流向

| | 讀 | 寫 |
|---|---|---|
| **agent** | 活 sandbox(永遠即時) | 活 sandbox,**節流鏡像 ≤5s** |
| **人類 UI(熱)** | 快照(最多舊 5s) | 活 sandbox + **write-through 立即鏡像** |
| **人類 UI(冷)** | 快照 | 快照(**不喚醒**) |

`refresh` = 強制 flush 待鏡像 + 重讀快照。

---

## 4 · Protocol / 元件改動

**Sandbox Protocol**(`sandbox/protocol.py`):
- `FileEntry.mtime: float` → `FileEntry.version: str`(不透明)。
- 新增:`delete`、`exists`、`mkdir`、`rmdir`、`rename`(三個 backend:Mock/Local/Docker 都要實作)。
- `walk` 每個 entry 回 `version`。
- (延後 issue #12:`upload_archive`/`download_archive`。)

**FileStore Protocol**(`filestore/protocol.py`):
- 刪除 `dirty_paths` / `clear_dirty`。
- 其餘維持(read/write/ls/exists/delete/mkdir/rmdir/is_dir/listdir)。

**`WorkspaceFiles` facade**(新):依「sandbox 死活」路由讀/寫到 sandbox 或 FileStore;持有 per-(workspace,path) 鎖供 CAS 用。agent 檔案工具 + API 檔案路由 + 人類寫都走它。

**SandboxSync → Mirror**(`sync/`):`flush` 砍掉;`restore` 維持(快照→sandbox on wake);`reverse` 改成 PULL 完整鏡像(`walk`+`version` diff、**含刪除**)+ ≤5s 節流器 + 強制 flush 入口。

---

## 5 · 喚醒 / 鏡像生命週期

- **wake**(first `exec` on cold):`registry.ensure_handle` → `create` + `restore`(全快照→sandbox,per-file)→ SoT=sandbox。
- **warm 期間**:agent 寫進 sandbox → 標記 workspace dirty + (重)上 5s timer;timer 觸發 → `walk`+`version` diff → 下載變更檔寫進快照 + 刪除消失的檔。人類寫 write-through(同步鏡像該 path)。
- **flush 入口**(立即跑待鏡像):`refresh` API、turn 結束、`kill_idle`、`close`、`close_all`。
- **idle-kill / close**:最後 flush + `kill` → SoT=快照。

---

## 6 · 寫入工具(P4)

- `edit_file(path, old_string, new_string)`:facade 讀當下 SoT、驗 `old_string` 唯一出現、取代、寫回(per-path lock 原子)。對不上(找不到 / 多重 / 檔案被改過)→ 回現況內容讓 agent 重讀重試。整檔重寫 = `old_string` 給整個現有內容。
- `write_file(path, content)`:**create-only**——已存在就拒絕並回現況(要改用 `edit_file`)。舊的盲寫 `write_file` 行為移除。
- **實作調整(對 Q7 的修正,誠實記錄)**:原訂 `write_file(expected_version)` 用不透明 `version` 做 CAS。實作時發現 **agent 無法預測 / 取得那個 opaque version**(Local/Docker 是 mtime-size,不是內容雜湊;cold 時 FileStore 根本沒 version),把它餵給 LLM 很彆扭。改用 **內容式 CAS**:`edit_file` 的 `old_string` 就是「預期的現況」,整檔重寫用整檔當 `old_string`。warm/cold 一致、LLM 友善、同樣達成「禁止閉眼寫 + 接住並行修改」。`write_file` 退化成 create-only(最強的「不覆蓋」)。若日後想要 version-CAS,需先把 version 透過 `read_file` 之類回給 agent。

---

## 7 · 分階段建置(每階段 TDD → gate → commit → 本表打勾)

### P1 · 地基(facade + Protocol 檔案操作 + version),行為不變  ✅
- [x] `FileEntry.version: str` 取代 `mtime`;`walk` 回 `version`。
- [x] Sandbox Protocol 新增 `delete`/`exists`/`mkdir`/`rmdir`/`rename`;MockSandbox + LocalProcessSandbox + DockerSandbox 各自實作 + `version`(Mock=content hash、Local=mtime_ns-size、Docker=find mtime-size)。
- [x] `WorkspaceFiles` facade:此階段**仍背 FileStore**(行為與今日一致),agent 工具 + API 檔案路由集中走 facade。
- [x] 既有測試全綠(468 passed,100% coverage)。

### P2 · 翻轉 SoT(facade 路由 + exec-only 喚醒)  ✅
- [x] facade 改「sandbox 熱→sandbox、冷→快照」路由(讀/寫/ls/exists/delete/mkdir/rmdir;is_dir/listdir 由 walk 推導),`registry.peek_handle` 判活。
- [x] 喚醒收斂成 **only `exec`**(`create` + `restore`);移除 exec 前的 flush;agent 檔案工具吃 facade。
- [x] 落差回歸測試:冷寫 `write_file` 經喚醒後 `exec` 看得到;`exec`/shell 建的檔 `read_file`/`ls` 看得到。
- [~] P2 先讓所有呼叫端(agent + 人類 API 路由)都走同一個 liveness facade(全體一致、修掉雙向落差);「人類讀改走便宜快照 + ≤5s」留到 P3 的節流鏡像一起做。

### P3 · 鏡像 PULL + 節流  ✅
- [x] `reverse` → `mirror`:PULL 完整鏡像(`walk`+`version` diff、**含刪除**、seed on restore);砍掉 `flush`/`dirty_paths`/`clear_dirty`(FileStore + SandboxSync)。
- [x] ≤5s 節流:背景 `_mirror_sweeper` 每 `mirror_interval` 對所有**熱** session 做 version-diff 鏡像(合併 agent 寫,且**抓得到 shell 寫的檔**——比 dirty-flag 健壯)。
- [x] `POST /investigations/{id}/files/refresh`(強制 flush);terminal-exec / idle-kill / close 都走 `registry.flush`/`mirror`。
- [x] sweeper 行為以測試固定(熱 session 的 shell 檔被鏡像進快照)。
- [~] **人類讀改走便宜快照 + refresh 重讀**:延後。目前人類 API 與 agent 共用 liveness facade(讀活 sandbox,正確但較貴);快照鏡像已就緒,切換成「人類讀快照」只是改路由,記為後續 — GitHub issue #13。

### P4 · CAS 寫入工具  ✅
- [x] `edit_file(old_string,new_string)`:唯一匹配才改、衝突回現況(整檔重寫 = 整檔當 old_string)。
- [x] `write_file(path,content)`:create-only、已存在回現況;移除盲寫。
- [x] CAS 在 facade per-(ws,path) `asyncio.Lock` 內原子;人類寫(API PUT)last-writer-wins(不帶 expected)。
- [x] agent-vs-人類並行編輯回歸測試:人類改檔 → agent 帶舊 `old_string` → 拒絕回現況 → 重讀重試 → 成功。
- 註:原 `expected_version`(不透明 version)改為**內容式 CAS**,理由見 §6。

---

## 8 · 標準限制(沿用 CLAUDE.md / memory)

- 回應用繁體中文(台灣用語);code/identifiers/commit messages 用英文。
- uv + pytest + ruff + ty + coverage.py(**不用 pytest-cov**);後端 **100% coverage**。
- 前端 vitest TDD;typecheck + build 綠。
- specstar 一律 `SpecStar()` 新實例;struct 欄位用 `dict[str, Any]`。
- **不 push 到 remote**(只在 local main commit);GitHub issue 已由使用者授權建立(#12)。
- 不改 `design_handoff_rca_3.0/`(唯讀參考)。
- commit footer:`Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`。
