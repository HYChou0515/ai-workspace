# Skill hub:歷史與回溯、下載/使用次數、問 AI 該裝哪個

**狀態:** grill 完成(2026-10-06 → 10-07),三個功能都已定案,等 user 點頭後依 Phases 施工。
來源標記:〔user〕= user 的原話或明確選擇;〔查證〕= 讀程式碼確認的事實;〔建議〕= 我提的、尚未確認。

## 要做的三件事〔user〕

1. 記錄並能顯示歷史,**能回溯**。
2. 記錄每個 skill 總共被下載 / 使用幾次。
3. 可以問 AI「我該安裝哪個 skill」。

## 現況〔查證,master `24e951d8`〕

- 重新發布 = `SkillHubEntry` 的一個新 specstar revision;轉移 owner、改可見範圍也各是一個 revision,沒有被 prune。
  revision 帶 `updated_by` / 時間;fork 記在 fork 那一條的 `forked_from`。資料在,沒有 API、沒有畫面。
- skill 的檔案每版寫進 `create_app` 收到的 `filestore`(= `sandbox_filestore`,`factories.py:get_sandbox_filestore`)
  底下一個新的命名空間 `skill-hub:<id>:<uuid>`,`SkillHubEntry.blobs` 記它;發布完 `purge(previous)` 刪掉上一版,
  所以舊版內容拿不回來。依 `sandbox.durable.kind`,這個位置可能是 specstar `WorkspaceFile`、NFS 檔案樹或兩者雙讀。
- 已安裝副本的 `.skill/<name>/.origin`(`SkillOrigin`:`source` / `files` = 每檔 sha256 / `entry`)有四個寫入者:
  `materialize_skill`、`install_hub_skill`、`refresh_skill`、`publish_skill`;讀它的是 skill 索引(每個 turn,只用
  `source`)、`skill_upstream`(「有新版」= `origin.files` ≠ 上游的 map)、`refresh_skill`(逐檔三方比對:沒改過的換、
  改過的跳過並回報、上游刪掉的只在沒改過時刪)、`skill_folder_in_the_way`、`publish_skill`(指向別人條目就拒絕)。
- 單一條目只有總大小上限 `SKILL_HUB_MAX_BYTES` = 20 MB,**沒有檔案數上限**。
- API image(`docker/Dockerfile`)沒有 `git`,Python 依賴裡也沒有 git 套件。
- item 裡的 agent 已經有 `search_skill_hub` / `install_skill`,只在 item 聊天裡;skill hub 頁面本身不能問 AI。
- 既有缺陷:`publish` 先 `find(owner, name)` 再 `mint_entry_id()`,同一個人同時發布兩次同名的**新** skill 會建出
  兩條同 `owner/name` 的條目。

## 已定案:功能 1(歷史與回溯)

| # | 決定 | 來源 |
|---|---|---|
| G1 | 版本用 **git** 管理 | 〔user〕「直接在 skillhubentry 裡面放檔案路徑和 git commit…使用 git 管理」 |
| G2 | git 放在**自己設定的目錄**(新設定 `skill_hub.git_root`),不跟 sandbox 共用 | 〔user〕 |
| G3 | API image 裝 `git` | 〔user〕 |
| G4 | 一個 skill 一個 bare repo:`{git_root}/<entry id>.git` | 〔建議,實作細節〕 |
| G5 | **`master` = 目前版本**;搶鎖用 push:`git push --force-with-lease=master:<讀到的舊 master>`,被拒就是沒搶到。發布被拒 → 重讀、重做 commit、再推(有上限);回復被拒 → 告訴 owner「版本剛被別人改過」,不自動重試 | 〔user〕「搶鎖還是用 push 因為爭的是 master 的位置」 |
| G6 | 回復 = `master` 指回舊 commit(git 層的 switch);之後的發布**接在目前版本後面**,被退掉的版本成為旁支 | 〔user〕「Switch 比較好」、選 A |
| G7 | `SkillHubEntry` 加 `commit`(這個 revision 當下 `master` 的 commit);**每個 revision 打一個 tag `r-<revision id>`** 指向它的 commit,revision ↔ commit 雙向可查;tag 也讓旁支 commit 不被 gc | 〔user〕「直接放 revision 做 tag 比較好,雙向 link 得到」 |
| G8 | 寫入順序:push 成功(搶到)→ `update` `SkillHubEntry` → 打 tag。push 成功但後兩步沒做完 → 下次讀到 `master` 的 commit 沒有 `r-` tag 就補做 | 〔建議〕 |
| G9 | 不設保留上限,超過再說 | 〔user〕「不需要了,要超過再說」 |
| G10 | 要備份 git 目錄,也要把**已發布的條目**搬進 git(每條建 repo,目前內容做成第一個 commit) | 〔user〕 |
| G11 | 單一條目**檔案數上限 1000**,和 20 MB 上限放在同一個檢查 | 〔user〕「1000 個檔案算是很鬆了」 |
| G12 | skill hub 副本的 `.origin` 改記 `{source: "hub", entry, commit}`;shared / profile 副本維持 `files` map(它們沒有 git,基準只能存在副本裡);`SkillHubEntry` 不再存 `origin` map,檔案清單從 `git ls-tree` 讀 | 〔user〕「好 ok」 |
| G13 | 「有沒有變」= 副本的 `commit` ≠ skill hub 的 `master`,不碰檔案 | 〔user〕(同 G12) |
| G14 | 同步(refresh)的逐檔三方比對:基準 = `git ls-tree -r -l <副本的 commit>`,上游 = `git ls-tree -r -l master`,副本 = 自己用 git blob 公式算。**不 clone、不 checkout**;只有要寫進副本的檔案才 `git cat-file` 讀內容 | 〔user〕「ok」(比 clone+checkout+diff 兩個資料夾省掉全部的暫存寫入) |
| G15 | **LFS 第一天就有**,用 LFS 的正規方式:`.gitattributes` 以**路徑模式**決定,清單由平台固定(圖片 `*.png *.jpg *.jpeg *.gif *.webp`、文件 `*.pdf *.docx *.xlsx *.pptx`、壓縮檔 `*.zip *.gz *.tar`),發布者不能改。不用檔案大小決定 | 〔user〕「Day 1 就要有 lfs 比較規則」「Lfs 就不能用大小做門檻」 |
| G16 | 比對順序:先全部用 blob id 比;對不上、而 repo 那邊是小 blob 的,用一次 `git cat-file --batch` 讀出來看是不是 LFS 指標(`version https://git-lfs`),是的話改比指標裡的 `size` 與 `oid sha256`。不靠解析 `.gitattributes` 判斷 | 〔user〕「Ok」 |
| G17 | 舊的 skill hub 副本(`.origin` 只有 sha256 map、沒有 `commit`)照舊比對,按一次同步後換成新格式 | 〔建議〕 |
| G18 | 回復 = `update` 只改跟內容走的欄位:`commit`、`description`、`review`、`referenced_tools`,值從那個舊 commit 當時的 revision 讀回來(`review` 是當時 AI 的意見,算不回來);owner、可見範圍維持現值。**不用** specstar `switch`(它會把 owner / 可見範圍一起換回舊值) | 〔user〕「可以」 |
| G19 | 回復的權限**和「修改」相同**:同一個檢查(現在是 `skill_hub_routes._owned`,= owner);「修改」日後放寬,回復跟著放寬 | 〔user〕「回覆跟修改權限一樣」 |
| G20 | 回復**不重新** AI 審查 | 〔user〕「對 不用」 |
| G21 | 已安裝副本在 skill hub 上的條目變更後(新發布或回復都一樣)顯示「**skill 已變更**」+〔**同步**〕;想知道原因到 skill hub 詳情頁看時間軸 | 〔user〕「提示:skill 已變更(同步)」 |
| G22 | 一律寫「skill hub」,不簡寫成「hub」(介面、文件、註解) | 〔user〕(重申 `plan-skill-hub.md` D8) |
| G23 | 其他人**不能**直接安裝舊版;能看任一版內容、比對兩版、從某一版 fork | 〔user〕「對 不能」 |
| G24 | skill hub 詳情頁一條時間軸,每個 revision 一筆:發布(誰、時間、說明、審查意見)、回復、轉移 owner —— 看得到這個 skill 的人都看得到;下架 / 重新上架 / 改可見範圍(含名單)**只有 owner 看得到**。被 fork 不進時間軸(詳情頁已有 fork 清單) | 〔user〕「好」 |
| G25 | 不做 repo 大小的畫面;在 `docs/deployment.md` 寫運營方查看與整理的指令(`du -sh {git_root}/*.git`、`count-objects -vH`、`gc`),`git_root` 寫進 `docs/configuration.md` | 〔user〕「不用 在文檔說明我要怎麼用指令看就好了」 |
| G26 | 修掉「同時首次發布同名 skill 產生兩條」:**建立條目 → 用 `find(owner, name)` 檢查 → 看到別人就刪掉自己,隨機等待後重試(上限 5 次,超過回報請稍後再試)**。不看時間(各 pod 時鐘有誤差,時間規則會兩條都留下);不加 pending 狀態、不加名字登記。後建立的那一方的檢查一定看得到對方,所以不會留下兩條;最壞是兩條都刪,靠重試補回。**git repo、push、tag、回報成功都在檢查通過之後才做**,被刪的只是一筆列 | 〔user〕「可以」(規則 1) |

### 推翻的決定

- 本計畫前一版的 H1(不用 git)、H2(保留 1000 版,用 specstar `prune_revisions`)、H3(檔案搬進
  `SkillHubEntry.files`,舊資料雙讀)與其相容做法,被 G1–G10 取代〔user 後來改走 git〕。
- `plan-skill-hub.md` 寫「payload 的每個檔案存成 blob(走既有 FileStore,一個 skill hub 命名空間),不塞進 struct」、
  「重新發布 = specstar 原生 revision」:G1 / G7 推翻其儲存方式。依 CLAUDE.md,實作的 PR 要在 `plan-skill-hub.md`
  標題下加 `> 被 #<那個 PR>（plan-skill-hub-history.md）推翻`。

## 已定案:功能 2(下載 / 使用次數)

| # | 決定 | 來源 |
|---|---|---|
| U1 | 「下載數」= **安裝次數**(每裝進一個 item 算一次);「使用數」= 已安裝副本被 `read_skill` 讀一次算一次;zip 下載、同步都不算 | 〔建議〕,user 追問延遲後接受 |
| U2 | 不在請求路徑上寫資料庫:`read_skill` / 安裝只在**這個 pod 的記憶體**加一;條目 id 取自 skill 索引每個 turn 本來就讀的 `.origin`,不多讀檔 | 〔user〕「會不會造成延遲」之後的形狀 |
| U3 | 計數**不放在 `SkillHubEntry`**(會灌爆 revision、時間軸與 tag);新 resource `SkillHubUsage`,id = `<entry id>∕<日期>∕<pod>`,一個 pod、一個 skill、一天一筆,原地 `update` 後立刻 `prune_revisions(keep_last_n=1)`;id 含 pod,所以每筆只有一個寫入者 | 〔user〕「update 這個計數器可能會讓計數器 revision 數量暴漲」 |
| U4 | 每 **2 小時**寫出一次;pod 收到 SIGTERM 時先寫出再結束(掛在既有 graceful shutdown)。只有 pod 直接當掉才會掉最多 2 小時;畫面數字最多落後 2 小時 | 〔user〕「也許 2 小時一次」 |
| U5 | 每筆記 `installs` / `uses` 總數,外加依「**人 + item**」分開的次數;**只記不顯示**,沒有 API 回傳 | 〔user〕「紀錄上可以放 只是顯示不用」「Ok」 |
| U6 | skill hub 列表卡片與詳情頁顯示「安裝 N 次 · 使用 M 次」,看得到這個 skill 的人都看得到;列表多「最常使用」排序;fork 各自計數 | 〔user〕「對」 |
| U7 | **不補算**上線前的安裝;數字旁寫「自 <上線日> 起」 | 〔user〕「不補」 |

## 已定案:功能 3(問 AI 該裝哪個)

| # | 決定 | 來源 |
|---|---|---|
| A1 | 只在 **item 聊天**裡問;skill hub 頁面本身**不放任何 AI 入口** | 〔user〕「Item 聊天即可 skillhub 頁面本身不讓碰 ai」 |
| A2 | `search_skill_hub` 每筆多回:安裝 / 使用次數、這個 item 是否已裝、最後更新時間、AI 審查有沒有意見;比對方式維持關鍵字(不做語意搜尋) | 〔user〕「Ok」 |
| A3 | 新 tool `show_skill_hub_entry(entry_id)`:聊天裡出現一張**即時**卡片(名稱、owner、說明、次數、這個 App 缺的 tool、審查意見、〔安裝〕),〔安裝〕走 Skills 面板同一條安裝流程;已裝則顯示「已安裝」;列進系統提示的 `## Available views` 索引 | 〔user〕「Ok」 |

## Phases

每個 phase 先寫會在未修碼上變紅的測試(`/tdd`)。

| P | 做什麼 |
|---|---|
| P1 | git 儲存核心:設定 `skill_hub.git_root`、image 裝 `git`;每個條目一個 bare repo;寫 commit(一般檔 + LFS 指標 + 平台固定的 `.gitattributes`)、讀 `ls-tree` / `cat-file`;blob id 與 LFS 指標的比對(G14–G16)。純函式為主,真 git 跑在 tmp 目錄 |
| P2 | 發布改走 git:`master` push lease 搶鎖(G5)、`SkillHubEntry.commit` + 每個 revision 的 `r-` tag(G7)、寫入順序與補做(G8)、檔案數上限 1000(G11)、首次發布同名的檢查後刪除重試(G26) |
| P3 | 讀取改走 git:安裝、同步(三方比對)、「有沒有變」;skill hub 副本 `.origin` 記 `commit`(G12、G13);舊格式副本照舊比對(G17);詳情頁檔案清單改用 `ls-tree`;`SkillHubEntry.origin` 拿掉 |
| P4 | 既有條目搬進 git(G10):每條建 repo、目前內容做成第一個 commit;`docs/migrations.md` 一條(設定 `git_root`、備份、搬移指令、確認做完) |
| P5 | 回復:路由與權限(與「修改」同一個檢查,G19)、`master` 指回舊 commit、`update` 只改內容欄位並從舊 revision 讀回(G18)、不重新審查(G20)、lease 被拒時的說明 |
| P6 | 歷史 API:時間軸(可見範圍事件只給 owner,G24)、看任一版內容、比對兩版、從某一版 fork(G23) |
| P7 | 前端:詳情頁時間軸、看舊版 / 比對、〔回復〕、〔從這一版 fork〕;副本提示「skill 已變更」〔同步〕(G21);一律寫「skill hub」(G22)。真 Chromium 量 1280 / 390 |
| P8 | 使用次數:記憶體計數、2 小時與 SIGTERM 寫出、`SkillHubUsage`(U2–U5);列表與詳情頁顯示、「最常使用」排序、「自 <日期> 起」(U6、U7) |
| P9 | AI:`search_skill_hub` 多回的欄位(A2)、`show_skill_hub_entry` 與聊天卡片、`## Available views`(A3);`skill-hub` 這個 skill 的指引與 `sample-scenarios/` 情境 |
| P10 | 文件:`docs/deployment.md` 的查看 / 整理指令(G25)、`docs/configuration.md` 的 `git_root`、`docs/extending-the-platform.md`;在 `plan-skill-hub.md` 加「被 #<PR> 推翻」 |
