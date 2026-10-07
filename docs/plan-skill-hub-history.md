# Skill hub:歷史與回溯、下載/使用次數、問 AI 該裝哪個

**狀態:** grill 完成(2026-10-06 → 10-07);§7 三題待 user 決定;之後依 §9 Phases 施工。
**來源標記:** 〔user〕= user 的原話或明確選擇;〔查證〕= 讀程式碼確認的事實;〔建議〕= 我提的、user 未明確表態。

## 1. 要做的三件事〔user〕

1. 記錄並能顯示歷史,**能回溯**。
2. 記錄每個 skill 總共被下載 / 使用幾次。
3. 可以問 AI「我該安裝哪個 skill」。

## 2. 現況〔查證,master `24e951d8`〕

- **版本:** 重新發布、轉移 owner、改可見範圍各是 `SkillHubEntry` 的一個 specstar revision(帶 `updated_by` /
  時間,沒被 prune)。資料在,沒有 API、沒有畫面。
- **檔案:** 每版寫進 `sandbox_filestore`(`factories.py:get_sandbox_filestore`)下新的命名空間
  `skill-hub:<id>:<uuid>`,`SkillHubEntry.blobs` 指向它;發布完 `purge(previous)`,**舊版內容拿不回來**。
  實際位置依 `sandbox.durable.kind`:specstar `WorkspaceFile`、NFS 檔案樹,或兩者雙讀。
- **副本:** `.skill/<name>/.origin`(`SkillOrigin` = `source` / `files`〔每檔 sha256〕/ `entry`)。
  寫入者:`materialize_skill`、`install_hub_skill`、`refresh_skill`、`publish_skill`。
  讀取者:skill 索引(每個 turn,只用 `source`)、`skill_upstream`(`origin.files` ≠ 上游 → 有新版)、
  `refresh_skill`(逐檔三方比對,改過的不蓋)、`skill_folder_in_the_way`、`publish_skill`。
- **上限:** 單一條目 20 MB(`SKILL_HUB_MAX_BYTES`),沒有檔案數上限。
- **權限:** 修改 / 下架 / 轉移 / 改可見範圍都走 `skill_hub_routes._owned`(= owner)。
- **AI:** item 聊天裡已有 `search_skill_hub`(名稱 + 說明的子字串比對)與 `install_skill`;skill hub 頁面沒有 AI。
- **image:** `docker/Dockerfile` 沒有 `git`,Python 依賴裡也沒有 git 套件。
- **既有缺陷:** `publish` 先 `find(owner, name)` 再 `mint_entry_id()`;同一個人同時發布兩次同名的新 skill
  會建出兩條同 `owner/name` 的條目。

## 3. 資料

### 3.1 git(G1–G4、G7、G15)

```
{skill_hub.git_root}/                  # 新設定;不和 sandbox 共用
  <entry id>.git/                      # 一個 skill 一個 bare repo
    refs/heads/master                  # 目前版本;搶鎖就是搶它
    refs/tags/r-<revision id>          # SkillHubEntry 的每個 revision 一個,指向當時的 master
    lfs/objects/<2>/<2>/<sha256>       # LFS 物件
```

- commit 的樹 = skill 資料夾(`SKILL.md`、`references/…`)+ 平台產生的 `.gitattributes`。
- `.gitattributes` 由平台固定,發布者不能改:
  `*.png *.jpg *.jpeg *.gif *.webp *.pdf *.docx *.xlsx *.pptx *.zip *.gz *.tar` → `filter=lfs diff=lfs merge=lfs -text`。
  符合的檔案在樹裡是 LFS 指標(`version` / `oid sha256:` / `size` 三行),內容在 `lfs/objects/`。

### 3.2 specstar

```python
class SkillHubEntry(Struct):          # 既有;改動以 + / - 標示
    owner: str
    name: str
    description: str                  # 跟著內容走
    source_item: str
    source_app: str
    source_profile: str
    review: SkillHubReview            # 跟著內容走
    forked_from: str = ""
    referenced_tools: list[str] = []  # 跟著內容走
    permission: Permission
  + commit: str = ""                  # 這個 revision 當下 master 的 commit;"" = 還沒推上 git(見 §4.1)
  - origin: SkillOrigin               # 拿掉:檔案清單與 hash 從 git 讀
  - blobs: str = ""                   # 拿掉:搬移完成後不再需要(§6)

class SkillHubUsage(Struct):          # 新;resource id = <entry id>∕<日期>∕<pod>
    entry_id: str                     # 有索引
    day: str                          # "2026-10-07"
    pod: str
    installs: int
    uses: int
    by_user_item: dict[str, UserItemUsage]   # key = "<user>∕<item id>";只記不顯示
```

`SkillHubUsage` post-`spec.apply` 註冊,沒有 auto-CRUD。

### 3.3 副本的 `.origin`(G12、G17)

| 來源 | 內容 | 「當初出貨的樣子」從哪拿 |
|---|---|---|
| skill hub(新) | `{source: "hub", entry, commit}` | `git ls-tree -r -l <commit>` |
| skill hub(舊格式) | `{source: "hub", entry, files: {路徑: sha256}}` | `files`;同步一次後換成新格式 |
| shared / profile | `{source, files: {路徑: sha256}}`(不變) | `files`(上游在 image 裡,沒有 git) |

## 4. 流程

### 4.1 發布新 skill(G5、G7、G8、G11、G26)

1. 檢查大小 ≤ 20 MB、檔案數 ≤ 1000;AI 審查(既有)。
2. `create` `SkillHubEntry`(新 uuid,`commit = ""`)。
3. `find(owner, name)`:除了自己還有別的 → 刪掉自己,隨機等待後從第 2 步重試(上限 5 次,超過回報「同時有人在
   發布同名的 skill,請稍後再試」)。
4. 建 repo `<entry id>.git`,commit,`git push --force-with-lease=master:`(master 必須還不存在)。
5. `update` 條目(`commit` = 新 commit)→ 打 tag `r-<這個 revision>`。
6. 回報發布成功。

`commit == ""` 的條目不出現在列表、搜尋、安裝與 `find` 以外的任何地方。

### 4.2 重新發布(G5)

1. 讀 `master` → 在它上面做新 commit。
2. `git push --force-with-lease=master:<讀到的 master>`;被拒 → 回第 1 步(上限)。
3. `update` 條目(`commit` 與跟著內容走的欄位)→ 打 tag。

### 4.3 回復(G6、G18–G20)

1. 權限:與「修改」同一個檢查(`_owned`)。
2. owner 選一個 revision R → 取 R 的 `commit`。
3. `git push --force-with-lease=master:<目前 master>`,把 master 指向 R 的 commit;被拒 → 告訴 owner
   「版本剛被別人改過,請重新確認」,不自動重試。
4. `update` 條目:`commit`、`description`、`review`、`referenced_tools` 取 R 的值;owner、可見範圍維持現值。
   **不用** specstar `switch`(它會把 owner / 可見範圍一起換回 R 的值)。不重新 AI 審查。
5. 打 tag。之後的發布接在這個 commit 後面;被退掉的版本成為旁支,靠 tag 保留。

### 4.4 轉移 owner、下架 / 上架、改可見範圍

`update` 條目(master 不動)→ 打 tag 指向目前 master。(轉移時對方已有同名 skill 的情況見 §7 Q1。)

### 4.5 安裝(G12、U2)

`git ls-tree -r master` → `git cat-file` 讀出每個檔案(LFS 指標換成 `lfs/objects/` 的內容)→ 寫進
`.skill/<name>/`(既有:先檢查空間、`.origin` 最後寫)→ `.origin = {source: "hub", entry, commit: master}`
→ 這個 pod 的記憶體計數 `installs + 1`。

### 4.6 「有沒有變」(G13、G21)

副本的 `commit` ≠ 條目目前的 `commit` → 顯示「**skill 已變更**」〔**同步**〕。不碰檔案;新發布和回復一樣處理。

### 4.7 同步(G14、G16)

1. 基準 = `git ls-tree -r -l <副本的 commit>`;上游 = `git ls-tree -r -l master`。不 clone、不 checkout。
2. 副本的每個檔案用 git blob 公式(`sha1("blob <len>\0" + 內容)`)算 blob id,和基準比:
   相同 → 沒改過;不同而基準那邊是小 blob → 一次 `git cat-file --batch` 讀出,是 LFS 指標就改比
   `size` 與 `oid sha256`。
3. 逐檔決定(既有規則):上游沒變 → 不動;上游變了且副本沒改過 → 換;副本改過 → 跳過並回報;
   上游刪掉且副本沒改過 → 刪。只有要寫的檔案才 `git cat-file` 讀內容。
4. `.origin.commit` = master。

### 4.8 計數(U1–U5)

- **安裝**(§4.5)與 **`read_skill` 讀到 skill hub 副本**時,只在這個 pod 的記憶體加一;條目 id 取自 skill 索引
  每個 turn 本來就讀的 `.origin`,不多讀檔。同步、zip 下載不算。
- **每 2 小時**,以及 pod 收到 **SIGTERM** 時(掛在既有 graceful shutdown):對每個有累積的條目,把數字加到
  `<entry id>∕<日期>∕<這個 pod>` 那一筆(`update`;不存在就 `create`)→ `prune_revisions(keep_last_n=1)`。
- 顯示總數 = 該條目所有 `SkillHubUsage` 的 `Sum`(以 `entry_id` 索引限定範圍)。

## 5. 每個窗口誰擋

| 情況 | 誰擋 / 怎麼收 |
|---|---|
| 兩個人同時重新發布同一個 skill | push lease:後推的被拒,重讀再推 |
| 回復時別人剛發布 | push lease:回復被拒,請 owner 重新確認 |
| 同一個人同時首次發布同名 skill | §4.1 第 3 步:後建立的那方的檢查一定看得到對方 → 刪掉自己;最壞兩方都刪,靠重試補回。不看時間(各 pod 時鐘有誤差) |
| push 成功但 `update` / tag 沒做完就掛了 | 下次讀到 master 的 commit 沒有對應 `r-` tag → 補做 `update` 與 tag(G8) |
| 首次發布在第 2–4 步之間掛了 | 留下一筆 `commit == ""` 的條目,不對外出現。但下次同名發布在第 3 步會看到它,於是刪掉自己、重試、再看到它……直到重試上限,永遠發布不了。處理方式見 §7 Q3 |
| 計數寫出時 pod 當掉 | 掉最多 2 小時的計數;SIGTERM 正常停機不掉 |
| 兩個 pod 寫同一筆計數 | 不會發生:id 含 pod 名稱 |

## 6. 搬移既有資料(G10、G17、U7)

- 每個既有條目:建 repo,把 `blobs` 指的現有內容做成第一個 commit,`update` 條目寫入 `commit`、打 tag。
  搬移時發現已經有兩條同 `owner/name` → 列出來給運營方處理,不自動合併。
- 舊的 skill hub 副本不動:`.origin` 沒有 `commit` 就照舊用 `files` 比對,同步一次後換成新格式。
- 計數不補算,從上線那天開始;畫面數字旁寫「自 <上線日> 起」。
- 運營方:設定 `skill_hub.git_root`、把它納入備份、跑一次搬移;寫進 `docs/migrations.md`。查看大小用
  `du -sh {git_root}/*.git`、`git -C <repo> count-objects -vH`,太大時 `git -C <repo> gc`(寫進
  `docs/deployment.md`,G25)。

## 7. 待決(grill 沒問到,重整計畫時發現)

| # | 問題 | 建議 |
|---|---|---|
| Q1 | 轉移 owner 時,對方已經有一個同名的 skill | 拒絕轉移並說明原因(否則會出現兩條同 `owner/name`,§4.1 的規則擋不到轉移) |
| Q2 | 刪除條目時 git repo 怎麼辦 | 和現在刪除時清掉檔案一致:soft delete 條目後刪掉 repo;已安裝副本照舊顯示「已刪除」 |
| Q3 | 首次發布中途掛掉留下的 `commit == ""` 殘骸,會讓之後同名的發布永遠失敗(§5) | §4.1 第 3 步檢查時,`commit == ""` 而且建立超過 10 分鐘的條目當作殘骸:刪掉它,不算「別人」。10 分鐘遠大於一次正常發布所需的時間 |

## 8. 介面

- **skill hub 詳情頁:** 一條時間軸,每個 revision 一筆(G24)。
  - 發布(誰、時間、說明、審查意見)、回復、轉移 owner:看得到這個 skill 的人都看得到。
  - 下架 / 重新上架 / 改可見範圍(含名單):**只有 owner** 看得到。
  - 被 fork 不進時間軸(詳情頁已有 fork 清單)。
  - 每一版能看內容、能和另一版比對;〔從這一版 fork〕。owner 另有〔回復到這一版〕。
  - 其他人**不能**直接安裝舊版(G23)。
- **已安裝副本:** 「skill 已變更」〔同步〕(G21)。
- **次數:** 列表卡片與詳情頁顯示「安裝 N 次 · 使用 M 次」,旁註「自 <上線日> 起」;列表多「最常使用」排序;
  fork 各自計數;不顯示是誰(U6、U7)。
- **AI(只在 item 聊天,A1):**
  - `search_skill_hub` 每筆多回:安裝 / 使用次數、這個 item 是否已裝、最後更新時間、AI 審查有沒有意見;
    比對方式維持關鍵字(A2)。
  - 新 tool `show_skill_hub_entry(entry_id)`:聊天裡一張即時卡片(名稱、owner、說明、次數、這個 App 缺的 tool、
    審查意見、〔安裝〕);〔安裝〕走 Skills 面板同一條安裝流程;已裝顯示「已安裝」;列進 `## Available views`(A3)。
- **用詞:** 一律寫「skill hub」,不簡寫成「hub」(G22)。

## 9. Phases

每個 phase 先寫會在未修碼上變紅的測試(`/tdd`)。

| P | 做什麼 |
|---|---|
| P1 | git 儲存核心:`skill_hub.git_root`、image 裝 `git`;建 repo、寫 commit(一般檔 + LFS 指標 + `.gitattributes`)、`ls-tree` / `cat-file`;blob id 與 LFS 指標比對(§3.1、§4.7 第 2 步)。真 git 跑在 tmp 目錄 |
| P2 | 發布:§4.1、§4.2、檔案數上限、§5 的補做、§7 Q3 的殘骸處理 |
| P3 | 安裝、「有沒有變」、同步改走 git(§4.5–§4.7);`.origin` 新格式與舊格式相容;詳情頁檔案清單改用 `ls-tree`;拿掉 `SkillHubEntry.origin` |
| P4 | 搬移既有資料(§6);`docs/migrations.md` 一條 |
| P5 | 回復(§4.3);轉移、下架、改可見範圍打 tag(§4.4);§7 Q1、Q2 的決定 |
| P6 | 歷史 API:時間軸(可見範圍事件只給 owner)、看任一版、比對兩版、從某一版 fork |
| P7 | 前端:詳情頁時間軸、看舊版 / 比對、〔回復〕、〔從這一版 fork〕、副本提示。真 Chromium 量 1280 / 390 |
| P8 | 計數(§4.8)、`SkillHubUsage`、列表與詳情頁顯示、「最常使用」排序 |
| P9 | AI:`search_skill_hub` 欄位、`show_skill_hub_entry` 與聊天卡片、`## Available views`;`skill-hub` skill 的指引與 `sample-scenarios/` 情境 |
| P10 | 文件:`docs/deployment.md`、`docs/configuration.md`、`docs/extending-the-platform.md`;`plan-skill-hub.md` 加「被 #<PR> 推翻」 |

## 10. 決策紀錄

| # | 決定 | 來源 |
|---|---|---|
| G1 | 版本用 git 管理 | 〔user〕「直接在 skillhubentry 裡面放檔案路徑和 git commit…使用 git 管理」 |
| G2 | git 放在自己設定的目錄,不和 sandbox 共用 | 〔user〕 |
| G3 | image 裝 `git` | 〔user〕 |
| G4 | 一個 skill 一個 bare repo | 〔建議〕 |
| G5 | `master` = 目前版本;push `--force-with-lease` 搶鎖,被拒就是沒搶到 | 〔user〕「搶鎖還是用 push 因為爭的是 master 的位置」 |
| G6 | 回復 = master 指回舊 commit;之後的發布接在目前版本後面 | 〔user〕「Switch 比較好」、選 A |
| G7 | `SkillHubEntry.commit` + 每個 revision 一個 tag `r-<revision id>`,雙向可查 | 〔user〕「直接放 revision 做 tag 比較好 雙向 link 得到」 |
| G8 | 寫入順序 push → `update` → tag;沒做完的下次補做 | 〔建議〕 |
| G9 | 不設保留上限,超過再說 | 〔user〕 |
| G10 | 備份 git 目錄;既有條目搬進 git | 〔user〕 |
| G11 | 檔案數上限 1000 | 〔user〕「1000 個檔案算是很鬆了」 |
| G12 | skill hub 副本 `.origin` 記 `commit`;shared / profile 維持 `files`;`SkillHubEntry` 不存 `origin` | 〔user〕「好 ok」 |
| G13 | 「有沒有變」= commit 不同,不碰檔案 | 〔user〕 |
| G14 | 同步用 `ls-tree` + 自算 blob id,不 clone / checkout | 〔user〕「ok」 |
| G15 | LFS 第一天就有;以平台固定的路徑模式決定,不用大小 | 〔user〕「Day 1 就要有 lfs 比較規則」「Lfs 就不能用大小做門檻」 |
| G16 | 先比 blob id,對不上的小 blob 再看是不是 LFS 指標;不解析 `.gitattributes` | 〔user〕「Ok」 |
| G17 | 舊格式副本照舊比對,同步後換新格式 | 〔建議〕 |
| G18 | 回復 = `update` 只改跟著內容走的欄位,值取自舊 revision;不用 specstar `switch` | 〔user〕「可以」 |
| G19 | 回復的權限和「修改」相同(同一個檢查) | 〔user〕「回覆跟修改權限一樣」 |
| G20 | 回復不重新 AI 審查 | 〔user〕「對 不用」 |
| G21 | 副本提示「skill 已變更」〔同步〕 | 〔user〕 |
| G22 | 一律寫「skill hub」 | 〔user〕(重申 `plan-skill-hub.md` D8) |
| G23 | 其他人不能直接裝舊版;能看、比對、fork | 〔user〕「對 不能」 |
| G24 | 時間軸內容與可見範圍(§8) | 〔user〕「好」 |
| G25 | repo 大小不做畫面,文件寫指令 | 〔user〕「不用 在文檔說明我要怎麼用指令看就好了」 |
| G26 | 首次發布同名:建立 → 檢查 → 看到別人就刪自己並重試;不看時間;其他一切在檢查通過後才做 | 〔user〕「可以」(規則 1) |
| U1 | 下載 = 安裝次數;使用 = `read_skill` 讀到副本的次數 | 〔建議〕(user 追問延遲後接受做法) |
| U2 | 請求路徑上只在記憶體加一 | 〔user〕「會不會造成延遲」之後的形狀 |
| U3 | 計數不放 `SkillHubEntry`;`SkillHubUsage` 每 pod 每 skill 每天一筆、只留一個 revision | 〔user〕「update 這個計數器可能會讓計數器 revision 數量暴漲」 |
| U4 | 每 2 小時寫出;SIGTERM 時先寫出 | 〔user〕「也許 2 小時一次」 |
| U5 | 記「人 + item」的次數,只記不顯示 | 〔user〕「紀錄上可以放 只是顯示不用」 |
| U6 | 列表與詳情頁顯示次數、「最常使用」排序、fork 各自計數 | 〔user〕「對」 |
| U7 | 不補算 | 〔user〕「不補」 |
| A1 | 只在 item 聊天問 AI;skill hub 頁面不放 AI | 〔user〕「Item 聊天即可 skillhub 頁面本身不讓碰 ai」 |
| A2 | `search_skill_hub` 多回四項;維持關鍵字比對 | 〔user〕「Ok」 |
| A3 | `show_skill_hub_entry` 即時卡片 | 〔user〕「Ok」 |

### 推翻的決定

- 本計畫早先的版本曾定「不用 git、用 specstar `prune_revisions` 保留 1000 版、檔案搬進 `SkillHubEntry.files`」,
  後來 user 改走 git(G1–G10)。
- `plan-skill-hub.md` 的「payload 的每個檔案存成 blob(走既有 FileStore,一個 skill hub 命名空間)」與
  「重新發布 = specstar 原生 revision」:儲存方式被 G1 / G7 推翻。依 CLAUDE.md,實作的 PR 要在 `plan-skill-hub.md`
  標題下加 `> 被 #<那個 PR>（plan-skill-hub-history.md）推翻`。
