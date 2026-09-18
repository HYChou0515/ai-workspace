# Skill Hub — 讓 skill 在人與人之間流動,不經過運營方的 git

**狀態:** grill 十題全部問過並定案(2026-09-18),已點頭,P1–P12 施工完成、第一輪四把鏡頭 review 的修正在 P13–P17、第二輪的在 P18(PR #818);施工中改掉的形狀已回寫在各段。**未做**:P11 的 `skill_eval --control` 實跑——夾具已補到能跑(三個 hub tool 的替身),但本機沒有跑得動的模型,對照組數字要部署方拿自己的模型跑。

> 第一版計劃把八個我自己決定的東西寫成「grill 收斂」,只有一題真的問過。這一版每一條
> 決定都是問了、答了才寫上去的;我建議但你改掉的,寫的是你的答案。

## 為什麼

skill 已經有三層,`read_skill` 的解析順序是 **workspace → shared → profile**
(`docs/extending-the-platform.md` §Skill):

| 層 | 放哪 | 誰能加 | 生效 |
|---|---|---|---|
| workspace | item 的 `.skill/<name>/SKILL.md` | 使用者,`save_skill` 或手放 | 每個 turn 即時重讀 |
| shared | `sample-skills/` + `SHARED_SKILLS` dict + `app.json` | 運營方進 git | 重新部署 |
| profile | `apps/<slug>/profiles/<p>/.skill/` | 運營方進 git | 重新部署 |

使用者做得出 skill(第一層),但它**被困在自己那個 workspace 裡**。要讓別人用,唯一的路
是運營方抄進 git 再部署——每一次分享都是一張 ops 票。這是 `plan-skills-and-tools.md`
§A.3 明列 v1 不做的第一條「全域 / 跨 template skill 池」。Skill hub 就是把那一條做起來。

`SKILL.md` 的格式是 Anthropic 的標準(frontmatter `name` + `description`),OpenCode、
Claude Code 讀同一個格式,所以帶出去不需要轉換。

## 你直接定的(不是問出來的)

| | |
|---|---|
| D1 | 前端要:skill hub 頁面(瀏覽 / 搜尋 / 詳情);**同時**多一個 `skill-hub` skill + tool 給 agent 用 |
| D2 | 裝、下載、上傳/發布這些**動作在 item 裡**(`SkillsModal`),skill hub 頁面不做這些 |
| D3 | 發布在 item 裡做,AI 審,審查結果出現在**對話窗**,有問題當場改、當場重發 |
| D4 | 審核兩級:結構性的擋、判斷性的放行掛意見 |
| D5 | `save_skill` 不變 |
| D6 | 修改 skill:按了開**當時發布的那個 item**去改;skill 在該 item 已不存在 → 先下載進去再開;item 已刪除或已完成 → 彈出開新 item |
| D7 | toolset 非常重要:skill 通常不能跨 app,因為 tool set 可能不同 |
| D8 | 名稱寫全:**skill hub**,不縮寫 |

## 決策表(每一題都問過)

| # | 問 | 答 |
|---|---|---|
| Q1 | skill hub 每個 app 一個,還是全站一個? | **全站一個**。裝進看起來不相容的 item **不拒絕,但要告知** |
| Q2 | 「需要哪些 tool」怎麼得知? | **程式碼掃 body 裡的已註冊 tool 名稱**(決定性、可測);AI 審查時的意見為輔,不進正式清單。裝的時候列「提到的 − 目標 app 有的」 |
| Q3 | 導入是複製還是訂閱? | **副本**。走既有 `materialize_skill` + `.origin` + `skill_update_available` + Refresh 按鈕 |
| Q4 | 誰能改? | **owner 是明確欄位,可轉移**(不用 `created_by`)。**非 owner 只能 fork**——fork = 發布一份 `.origin` 指向別人條目的副本,自動記 `forked_from`。身分是 **`owner/name`**,底層是穩定的 resource id。列表**根在上、fork 收在原作底下** |
| Q5 | 下架 / 刪除? | 平台現在**沒有**下架概念。**下架 = `visibility: private`(可逆)**;**刪除 = specstar soft delete(不可逆、不硬刪)**。兩者都**不動已裝副本**;所有指向它的地方(`.origin`、`forked_from`)**顯示狀態、不炸** |
| Q6 | 可見範圍? | **發布預設 public**;owner 可用既有 `Permission` 的 `restricted` + `read_content` 名單限縮 |
| Q7 | owner 的管理動作放哪? | **skill hub 詳情頁,owner 限定**:修改、下架/上架、刪除、轉移 owner、改可見範圍。其他人在詳情頁**沒有任何按鈕**。「我的」是列表的篩選 |
| Q8 | 給外部工具的 raw 網址? | **不做**。下載只有 item 裡既有的 zip |
| Q9 | AI 審查的模型掛了? | **429 等到過為止**(平台一貫做法,`failover/model.py` 的時間上限)。**審不到就不上架**——沒有 AI 等於系統壞了,此時不開缺口。沒有「未審」狀態 |
| Q10 | owner 轉移後新 owner 進不去舊來源 item? | **「進不去」= 「沒了」**:開新 item、下載進去、從那裡重新發布時來源指標更新成新 item |

直接後果(不另問,你不同意就說):**同一個 owner 用同一個 name 再發布 = 覆蓋成新 revision**,
不是新條目——這是 `owner/name` 當身分的必然結果。

## 資料模型

一個 specstar model `SkillHubEntry`(post-`spec.apply` 註冊,和 `_ScheduleIndex` 同法):

```
owner            str            # 明確欄位,可轉移;不是 created_by
name             str            # = 資料夾名 = frontmatter name;身分是 (owner, name)
description      str            # 從 frontmatter 取;agent 與人搜尋都靠它
source_item      str            # 發布時的 item id;「修改」開的就是它
source_app       str            # 那個 item 的 app slug(背景資訊,告知時顯示「在 app X 寫的」)
source_profile   str
origin           SkillOrigin    # origin_for("hub", payload) — 和 .origin manifest 同一個型別、同一個 hash
forked_from      str | None     # 原作的 resource id(穩定,不隨 owner 轉移變)
referenced_tools list[str]      # Q2:程式碼掃出來的
review           SkillHubReview # {verdict: "ok" | "notes", notes: list[str], model: str}
permission       Permission     # 既有結構;visibility 預設 public;private = 下架
```

payload 的每個檔案存成 blob(走既有 FileStore,一個 skill hub 命名空間),不塞進 struct。
重新發布 = specstar 原生 revision。刪除 = specstar soft delete,列留著。

`SkillOrigin` 要加一個欄位記 skill hub 的 resource id(現在只有 source 與 hash),
`SkillSource` 加 `"hub"`。

## 流程

### 發布(在 item 裡)

1. 人在對話說「把 `.skill/foo` 發布到 skill hub」,或按 `SkillsModal` 裡 workspace skill 那一列的
   「發布到 skill hub」(按鈕只是把那句話送進對話,和「Apply」載入下一輪同一種接法)
2. agent 呼叫 `publish_skill(name)`:
   - **先量大小再讀**:`stat_all` 拿 `.skill/<name>/` 每個檔的大小(不讀內容),總和超過
     `SKILL_HUB_MAX_BYTES`(20 MiB)就擋——第二輪 review 前這一條是讀完整個資料夾才算的。
     同一條規則在 `SkillHubStore.publish` 再守一次(列在那裡生出來,任何呼叫者都過不了);
     validator 本身不再算大小
   - 從 façade 讀 `.skill/<name>/` 成 payload
   - **結構驗**(擋):loader 本身的三條(frontmatter 能解析、`name` = 資料夾名、名字是 `.skill/` 底下
     **一層**資料夾——`a/b` 裝進去 loader 永遠列不到,規則用 loader 當 oracle 的 parity 表釘住;
     `.` / `..` 另外擋,因為真磁碟上 `.skill/../SKILL.md` 是 workspace 根)+ **hub 自己的**三條——
     `description` 不能空(loader 其實會列出沒 description 的 skill,但那正是「被列出卻永遠不觸發」的
     debug loop,所以 hub 刻意比 loader 嚴;parity 測試把這條分歧釘成有意的)、body ≤ `SKILL_BODY_CAP`、
     `references/` 存在性(內文提到的路徑問資料夾:`**references/g.md**` 指的是有出貨的
     `references/g.md`,不是靠一張標點清單)、`scripts/*.py` 能 `ast.parse`
   - **掃 tool**:body 裡的已註冊 tool 名,整字或 code span。註冊表是 `agent/tools.py` 的 `_IMPLS`
     (完整;本 PR 前 41 個,加上這三個後 44)——**不是** `TOOL_VERBS`,那張表只有 22 個,`read_skill` / `ask_user` / `kb_search`
     都不在裡面。掃描函式是純的,註冊表由呼叫端注入
   - **AI 審**(放行掛意見):審查者是**發布那個 turn 的 sub-agent**(`api/skill_review.py`
     的 `review_skill(runner, parent_ctx, folder, payload)`,底下是 `run_agent_task` 同一條
     `drive_subagent`),吃 turn 自己的 `AgentConfig`(`AppCatalog.resolve` 解出來的那份),
     所以模型、端點、failover 鏈、429 等待全是 turn 的,**沒有第二套 LLM 接線**。
     審查者無 tool、無歷史;回 `{"verdict","notes"}`,**verdict 由 notes 導出**(模型自填的不信),
     純文字回覆整段當一則 note(它審過了,只是格式不對;丟掉等於把格式失誤當放行)。
     連不上 / 回空 → `SkillReviewUnavailable`,tool 以錯誤結束,對話窗看到「發布失敗:審查服務無法連線」
   - 讀 `.origin`:指向 skill hub 上**別人的**條目 → 這是 fork,`forked_from` = 那個 id;
     指向自己的 → 覆蓋成新 revision;沒有 `.origin` 或指向 shared/profile → 新條目
   - 存 `SkillHubEntry`,`owner` = 現在的 user,`source_item` = 現在的 item,`visibility` = public
3. agent 的回話 = 審查結果:擋掉的說是哪條坑;放行的列 AI 意見和掃到的 tool

### 裝(在 item 裡)

1. `SkillsModal` 的「從 skill hub 裝」:開清單(搜尋、根在上 fork 收底下)、選一個
2. **告知**:顯示「在 app X 寫的;提到 `exec`、`ask_user`;這個 item 的 app 沒有 `ask_user`」——
   不擋,人決定
3. `install_skill(entry_id)` = `install_hub_skill`(`materialize_skill` 的 hub 版本:同一種副本形狀,`.origin` 記 entry id 與 hash;不是 `_skill_source` 的分支——見 P5 那列)
4. workspace 已有同名資料夾 → **拒絕**,說清楚「你已經有 alice 的 `triage-reflow`,先移除或改名」
5. 裝完下一個 turn 的 index 就有它;之後上游變了,「有新版」提示 + 既有 Refresh 按鈕

### 修改(skill hub 詳情頁,owner 限定)

按「修改」→ 解析 `source_item`,四個分支:

| 來源 item 的狀態 | 做什麼 |
|---|---|
| 讀得到、`.skill/<name>/` 還在 | 開那個 item |
| 讀得到、skill 不在了 | 先 `materialize_skill` 進去,再開 |
| 讀不到(轉移後**沒有 `edit_content`**——改 skill 要寫 item,唯讀不夠)、已刪除、或 status 在 app 的 `lifecycle.closing_states` 裡 | 彈出「開新 item」(帶 App 與 profile)→ 人在新 item 的 Skills 面板把它裝進去 → 改 → 從新 item 重新發布時 `source_item` 更新。施工結果:裝那一步是**人按**面板的「從 skill hub 裝」,不是系統自動下載——item 要先存在,而建 item 是 AppNewItem 的表單 |

### 下架 / 刪除 / 轉移 / 可見範圍(詳情頁,owner 限定)

- 下架:`permission.visibility = "private"`。列表與搜尋消失、`install_skill` 拒絕(依 Q10,措辭和「不存在」**同一句**,不說「已下架」——只有已裝副本那一列會顯示狀態)、
  已裝副本不動、fork 那一行顯示「原作已下架」。改回 public = 重新上架
- 刪除:soft delete。多一點:owner 自己列表也沒了;`.origin` / `forked_from` 查到
  `ResourceIsDeletedError` → 顯示「原作已刪除」。不硬刪、不給復原 UI
- 轉移:改 `owner`。entry id 不變,所以已裝副本與 fork 的指標都不斷
- 可見範圍:既有的權限編輯 UI

**不變量(測試釘住):** 任何指向 skill hub 條目的東西,在條目下架或刪除之後**不能炸,只能顯示狀態**。
施工時的形狀:`skill_update_available -> bool` 換成 `skill_upstream -> SkillUpstream(state, update_available)`,底下是 `resolve_upstream`——一個「副本的上游現在是什麼」的解析器,package skill 照舊按名字找、skill hub 副本按 `.origin.entry` 找,`state` 三態 `live / unpublished / deleted`(對這個 viewer 而言;unpublished = 條目還在但他讀不到);只有 `live` 會有 `update_available`。`refresh_skill` 走同一個解析器,非 live 就什麼都不動。面板列出時傳 `upstream` 欄位(P10 畫)。沒接 skill hub 卻遇到 hub 副本 → `ValueError`(接線錯誤要大聲,不能默默讀成 deleted)。

### Fork(沒有按鈕)

裝 → 改 → 發布 = fork。`.origin` 指向別人的條目就自動是 fork。

## 前端

**Skill hub 頁面**(全站):
- 列表:根在上,每條「N 個 fork」展開;搜尋 name / description;篩選「全部 / 我的」
- 詳情:SKILL.md 全文、owner、`owner/name`、在哪個 app 寫的、提到的 tool、AI 審查意見、
  `forked_from`(含原作狀態:正常 / 已下架 / 已刪除)、fork 清單
- **owner 才看得到**:修改、下架/上架、刪除、轉移 owner、可見範圍。**其他人沒有任何按鈕**

**`SkillsModal`**(item 內,既有):
- 新增「從 skill hub 裝」:清單 + 告知差集 + 裝
- workspace skill 那一列新增「發布到 skill hub」
- 「下載 zip」「Refresh」「本機匯入」都已存在,不動

## Agent 側

- `sample-skills/skill-hub/SKILL.md`,登記進 `SHARED_SKILLS`:什麼時候建議發布、怎麼幫人找、
  裝之前要先講告知與審查意見
- 三個 tool,薄殼,邏輯在 `apps/skill_hub.py`:

| tool | 授權 verb |
|---|---|
| `publish_skill(name)` | `edit_content`(和 `save_skill` 同) |
| `search_skill_hub(query)` | 讀 |
| `install_skill(entry_id)` | `edit_content` |

- `sample-scenarios/skill-hub/` 給 `skill_eval --control` 量觸發

## 能重用的

- `materialize_skill` — 裝;加一種 `"hub"` 來源
- `skill_update_available` — 有新版;加「下架 / 刪除」兩個狀態(→ 改名 `skill_upstream`,見上)
- `skill_payload` / `origin_for` — payload 與 hash;條目的 hash **就是**它算的
- `_workspace_skill_meta` — 結構驗證
- `Permission` + 既有權限編輯 UI — 可見範圍、下架
- `save_skill_impl` / `tool_authz.py` 的登記模式 — 三個新 tool
- `SkillsModal` — 兩顆按鈕的家;下載已經有
- `app.json` `lifecycle.closing_states` — 「已完成」的定義
- `failover/model.py` — 429 等待,靠走同一條 LLM 路徑繼承,不另做

## 階段

每一階段:先寫會紅的測試 → 綠 → 突變探針證明守衛會咬 → 推前跑三把鏡頭 → commit。

| | 做什麼 | 驗收 |
|---|---|---|
| **P1** | `SkillHubEntry` model + `apps/skill_hub.py` 存/取;`SkillSource` 加 `"hub"`;`SkillOrigin` 加 entry id | CRUD;同 owner 同 name 再存 = 新 revision;`origin` 等於 `origin_for("hub", payload)`(parity,`origin_for` 當 oracle) |
| **P2** | 結構驗證 + tool 掃描 | 每條規則各一個會紅的輸入;掃描對 `` `exec` `` 與整字都命中、對子字串不命中;通過的 payload 用 `materialize_skill` 裝進去後 `workspace_skill_metas` 真的列出它(真入口) |
| **P3** | AI 審:`review_skill(runner, parent_ctx, folder, payload) -> SkillHubReview`,turn 的 sub-agent(走 `drive_subagent`),`ScriptedAgentRunner` 測 | 模型連不上 / 回空 → 例外(不是「未審」);429 走 runner 自己的等待(用 `test_litellm_runner` 同款的 scripted-engine 夾具打真 `LitellmAgentRunner`);prompt 有上界(每檔 20k、總 100k,SKILL.md 不切) |
| **P4** | `publish_skill` tool + 授權 + fork 偵測 + source_item 記錄 | 從真 tool 入口打:結構壞 → 拒絕訊息說是哪條坑;`.origin` 指向別人 → `forked_from` 有值;指向自己 → revision;AI 有意見 → 發布成功且回話含意見 |
| **P5** | `install_skill` tool;`resolve_upstream`(hub 副本按 entry id 找,取代對 `_skill_source` 加分支——它是同步、按名字、走 Traversable 的);`skill_update_available` → `skill_upstream` 三態 | 裝完下一 turn 的 index 有它(真入口);同名已存在 → 拒絕不蓋、說是誰的;上游重發 → True;上游下架 / 刪除 → 顯示狀態、不炸(tool、apps 層、`GET /skills` 路由三層都釘) |
| **P6** | `search_skill_hub` tool + 列表/詳情 route(根+fork 巢狀、我的、tool 差集) | 依 description 命中;根列表不含 fork;差集對目標 app 算對;private 的不出現在別人的結果 |
| **P7** | 管理 route:下架/上架、刪除、轉移、可見範圍;owner 限定 | 非 owner 403;下架後 `install_skill` 拒絕;刪除後 `.origin` 解析為「已刪除」;轉移後 entry id 不變、副本不斷 |
| **P8** | 「修改」route:四分支解析 | 四個分支各一測;`closing_states` 從 app.json 讀不 hardcode |
| **P9** | FE:skill hub 頁面(列表 + 詳情 + owner 管理) | 非 owner 詳情頁零按鈕(mutation:owner 判斷反轉要紅);fork 巢狀;真瀏覽器量一次版面 |
| **P10** | FE:`SkillsModal` 兩顆按鈕 | 告知差集顯示;拒絕同名時訊息含對方 owner;發布按鈕送進對話 |
| **P11** | `skill-hub` meta-skill + scenarios + `SHARED_SKILLS` 登記 | 文件守衛;`skill_eval --control` 至少一個情境對照組不過、有 skill 過 |
| **P12** | 文件 + `migrations.md` | mkdocs --strict;migrations 條目四個框都有 |

## 不做(v1 之外)

- **fork 合回原作**——你的方向是**交給 AI、做成一個 skill**;這版不做
- fork 升格為原作、原作 owner 消失後的接管(只有 owner 轉移)
- 給外部工具的 raw 網址——Q8
- 硬刪除、刪除後的復原 UI——Q5
- agent 自動從 skill hub 安裝——D2 的精神:裝是人在 item 裡按的
- 同名衝突時的自動改名——拒絕並說明
- 評分、留言、下載計數
- 跨部署 federation
- FE 線上編輯 SKILL.md(修改一律回 item)

## 知情的風險

- **`SKILL.md` 是 prompt,惡意的 SKILL.md 可以教 agent 做壞事。** 和今天 workspace skill 沒有差別
  (使用者本來就能在自己的 workspace 放),skill hub 只是**擴大了受害範圍**——從自己到全站。
  AI 審的目的是減少 debug loop 不是保護系統;這是依「我們基本上不審」的信任模型**接受**的
  風險。`scripts/` 跑在 sandbox 裡,那半沒有新增暴露。
- **發布會等 AI 審完**,429 時可能等很久(時間上限:有 failover 鏈是 `failover.rate_limit_budget_s`,單一 endpoint 是 runner 內建的 120 秒——見 `docs/migrations.md` #818)。發布不是熱路徑,
  而且是刻意的:審不到就不上架。
- **`install_skill` 走配額**,超額大聲失敗,和 `materialize_skill` 今天一樣。
- **fork 之後的分歧不會自動收斂**——這是 fork 的本質,「有新版」提示是 v1 唯一的收斂力。

## migrations 帳(P12 要寫進 `docs/migrations.md`)

運營方要做的:新 model `SkillHubEntry`(post-apply 註冊,無 migrate);三個 tool 要在 `app.json`
`agent.tools` 授予;`skill-hub` 要在 `SHARED_SKILLS` / `app.json` `agent.skills` 打開。**沒做這些 =
功能存在但不動**,和 `trigger_check_interval_sec` 同一類。
