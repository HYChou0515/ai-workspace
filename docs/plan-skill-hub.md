# Skill Hub — 讓 skill 在人與人之間流動,不經過運營方的 git

**狀態:** grill 收斂,計劃待點頭(2026-09-18)。尚未動工。

## 為什麼

skill 已經有三層,`read_skill` 的解析順序是 **workspace → shared → profile**
(`docs/extending-the-platform.md` §Skill):

| 層 | 放哪 | 誰能加 | 生效 |
|---|---|---|---|
| workspace | item 的 `.skill/<name>/SKILL.md` | 使用者,`save_skill` 或手放 | 每個 turn 即時重讀 |
| shared | `sample-skills/` + `SHARED_SKILLS` dict + `app.json` | 運營方進 git | 重新部署 |
| profile | `apps/<slug>/profiles/<p>/.skill/` | 運營方進 git | 重新部署 |

使用者做得出 skill(第一層),但它**被困在自己那個 workspace 裡**。要讓別人用,唯一的路
是運營方抄進 git 再部署——每一次分享都是一張 ops 票。這正是 `plan-skills-and-tools.md`
§A.3 明列 v1 不做的第一條「全域 / 跨 template skill 池」。Hub 就是把那一條做起來:
使用者自己發布、自己找、自己裝,也能把 skill **帶出去**(OpenCode、Claude Code 讀的是同一個
`SKILL.md` 格式,所以帶出去不需要任何轉換)。

## 決策表(grill 收斂)

| # | 問 | 答 | 為什麼 |
|---|---|---|---|
| Q1 | 介面 | **不做前端頁面。** 一份 `skill-hub` meta-skill 教 agent 怎麼做,加三個 tool | 和 `author-skill`、`author-workflow` 同形:skill 教方法、tool 給能力。`save_skill` 一行不動 |
| Q2 | 導入是複製還是訂閱 | **複製**進 workspace 的 `.skill/<name>/` | loader 零改動、live 重讀與遮蔽規則照舊、使用者能改自己那份。hub 有新版只提示不自動蓋——`skill_update_available` 已經會做這件事 |
| Q3 | 審核 | **AI 審,兩級**:結構性的擋、判斷性的放行但掛意見 | 目的不是保護系統,是**減少 debug loop**。skill 最大的 debug loop 是「agent 為什麼沒用我的 skill」,答案永遠是三條靜默跳過的坑之一;AI 對「模糊」的誤判擋錯一份好 skill 比放過一份普通 skill 更煩 |
| Q4 | 可見範圍 | **v1 全站可見**,不做 group 範圍 | 「我們基本上不審」= 信任範圍是整個部署;group 之後是加一個欄位 |
| Q5 | 下載 | raw `SKILL.md` URL + zip archive;帶 `scripts/` 的標示「需要 workspace」 | 外部工具讀 raw 最方便;`scripts/` 依賴 sandbox 的 python-stack,拿到 OpenCode 跑不動,不標會變成另一個 debug loop |
| Q6 | agent 能不能自己從 hub 裝 | **不能。** v1 由人明確說「裝這個」 | 某人發布的 skill 被 agent 自動裝進別人的 item,是信任問題不是功能問題 |
| Q7 | 同名衝突 | name 是身分:同作者重發 = 新 revision;不同作者同名 = 拒絕 | 先到先得,不做命名空間 |
| Q8 | AI 審核時模型掛了 | 結構檢查照做;AI 那級標「未審」放行 | 「不保護系統」的邏輯一致:審不到不該擋人發布 |

## 三條靜默跳過的坑 = AI 要審的東西

文件裡寫明 loader 對壞掉的 skill 是**容忍**的:只 skip 那一個、不報錯、只 log warning。
所以「發布了但沒人用得到」不會有任何訊號。這三條就是 debug loop 的全部來源:

| 坑 | 誰驗 | 怎麼處置 |
|---|---|---|
| 資料夾名 ≠ frontmatter `name` | 程式碼(`_workspace_skill_meta` 已經在驗) | **擋** |
| 沒有 `description`、body > `SKILL_BODY_CAP` | 程式碼(同上) | **擋** |
| `description` 和人的問法對不上 | **AI**:只看 name + description,問「一個只看得到這兩行的 agent,聽到什麼樣的話會去讀它?」答不出來就是即將發生的 debug loop | 放行,掛意見 |
| body 只講「做什麼」沒講「怎麼做」 | AI | 放行,掛意見 |
| `references/` 指到的檔案不在 payload 裡 | 程式碼 | **擋**(裝了一定壞) |
| `scripts/*.py` 連 parse 都過不了 | 程式碼(`ast.parse`) | **擋** |

AI 那一級用 `AppCatalog.resolve` 拿模型——和 `skill_eval`、真正的 turn 同一條路,
不自己組 prompt(`skill_eval` 的教訓:自己組會漏掉東西而且沒有錯誤)。

## 能重用的(這是這個計劃小的原因)

- **`materialize_skill`**(`apps/skills.py`)已經是「把 skill 資料夾抄進 workspace」的完整
  實作:走 `WorkspaceFiles`(配額、warm/cold 路由都對)、有就不蓋、最後寫 `.origin` manifest
  記來源與 hash。**Install = 讓 `_skill_source` 多認得一種 `"hub"` 來源**,其餘不動。
- **`skill_update_available`** 靠 `.origin` 比對上游——hub 成為一種來源後,「hub 上有新版」
  免費。
- **`skill_payload`** 把資料夾變成 `{rel: bytes}`、**`origin_for`** 算 hash。HubSkill 存的
  hash **就是** `origin_for` 算出來的那個,不另算一套(「跟 X 一樣」要共用同一個函式)。
- **`_workspace_skill_meta(raw, dir_name)`** 是結構驗證器,publish 直接呼叫它。
- **`save_skill_impl`** 在 `agent/tools.py`、授權在 `agent/tool_authz.py`
  (`"save_skill": ("edit_content",)`)——三個新 tool 照同一個模式登記與授權。
- 下載端點的 auth 照 `read_skill` 的:能讀 item 的人能讀 hub。

## 範圍(v1)

### 資料

一個 specstar model `HubSkill`(post-`spec.apply` 註冊,和 `_ScheduleIndex` 同法):

```
name         str   # 身分;= 資料夾名 = frontmatter name
description  str   # 從 frontmatter 取,agent 與人搜尋都靠它
author       str   # 發布者 user id
origin       SkillOrigin  # origin_for("hub", payload) — 和 .origin manifest 同一個型別
portable     bool  # payload 只有 SKILL.md(不帶 scripts/)
review       HubReview   # {verdict: "ok"|"notes"|"unreviewed", notes: list[str], model: str}
```

payload 的每個檔案存成 blob(走既有的 FileStore,一個 `hub` 命名空間),不塞進 struct。
重新發布 = specstar 原生 revision,不自建版本表。

### 三個 tool(+ 授權)

| tool | 做什麼 | 授權 verb |
|---|---|---|
| `publish_skill(name)` | 讀 workspace `.skill/<name>/` → 結構驗(擋)→ AI 審(掛)→ 存 HubSkill | `edit_content`(和 `save_skill` 同) |
| `search_hub(query)` | 依 name / description 搜,回 name + description + author + portable + review.verdict | 讀 |
| `install_skill(name)` | `materialize_skill(..., source="hub")`;workspace 已有同名 → 拒絕並說明(不蓋) | `edit_content` |

三個都是薄殼:邏輯在 `apps/skill_hub.py`,tool 只做參數與回話。

### 兩個 HTTP 端點

- `GET /api/skills/hub/{name}/SKILL.md` — raw,給 OpenCode / Claude Code 直接拿
- `GET /api/skills/hub/{name}/archive` — zip 整個資料夾

回應標頭帶 `X-Skill-Portable: true|false`;非 portable 的 SKILL.md 回應在最上方**注入一行註記**
(不改存的內容,只改回應)說明它需要 workspace 的 python-stack。

### `skill-hub` meta-skill

`sample-skills/skill-hub/SKILL.md`,登記進 `SHARED_SKILLS`,教 agent:什麼時候該建議發布、
怎麼幫人找、裝之前要先講 review 意見、下載連結怎麼給。附 `sample-scenarios/skill-hub/`
讓 `skill_eval` 能量它會不會觸發。

### 文件與 migrations 帳

- `docs/extending-the-platform.md` §Skill 加第四種來源
- **`docs/migrations.md` 一定要有一條**:新 model(`HubSkill`)、三個要在 `app.json` 授予的 tool、
  一個要在 `SHARED_SKILLS` / `app.json` 打開的 skill——沒做這些 = 功能存在但不動,和
  `trigger_check_interval_sec` 是同一類

## 階段

每一階段:先寫會紅的測試 → 綠 → 突變探針證明守衛會咬 → 推前跑三把鏡頭 → commit。

| | 做什麼 | 驗收 |
|---|---|---|
| **P1** | `HubSkill` model + `apps/skill_hub.py` 的存/取/搜 | CRUD;同作者重發 = 新 revision、他人同名 = 拒絕;`origin` 等於 `origin_for("hub", payload)`(parity 測試,`origin_for` 當 oracle) |
| **P2** | 結構驗證:重用 `_workspace_skill_meta` + 補 references 存在性 + scripts `ast.parse` | 每條規則各一個會紅的輸入;通過的 payload 用 `materialize_skill` 裝進去後 `workspace_skill_metas` 真的列出它(真入口) |
| **P3** | AI 審:`review_skill(payload) -> HubReview`,`AppCatalog.resolve` 取模型,`ScriptedAgentRunner` 測 | 模型掛 → `unreviewed` 而非例外;意見存進 HubSkill |
| **P4** | `publish_skill` tool + 授權 | 從真的 tool 入口打:結構壞 → 拒絕訊息說出是哪條坑;AI 有意見 → 發布成功且回話含意見 |
| **P5** | `_skill_source` 認得 `"hub"`;`install_skill` tool;`skill_update_available` 涵蓋 hub | 裝完**下一個 turn 的 index 有它**(真入口);同名已存在 → 拒絕不蓋;hub 重發後 `skill_update_available` 回 True |
| **P6** | `search_hub` tool | 依 description 命中;回覆含 review.verdict 與 portable |
| **P7** | 兩個 HTTP 端點 | 未登入 401;non-portable 的 raw 回應含注記;zip 解開 = payload |
| **P8** | `skill-hub` meta-skill + scenarios + `SHARED_SKILLS` 登記 | 文件守衛(範例能過 P2 的驗證);`skill_eval --control` 至少一個情境對照組不過、有 skill 過 |
| **P9** | 文件 + migrations.md | mkdocs --strict;migrations 條目四個框都有 |

## 不做(v1 之外)

- 前端 hub 頁面(list / detail)——Q1
- 訂閱 / 自動更新——Q2;`skill_update_available` 只提示
- agent 自動安裝——Q6
- group 範圍的可見性——Q4
- 評分、留言、下載計數
- 跨部署 federation / 從外部 URL 直接 install
- 從 hub 下架後回收已裝的副本(副本就是副本)
- FE 線上編輯 skill(v1 就 defer,維持)

## 知情的風險

- **`SKILL.md` 是 prompt,惡意的 SKILL.md 可以教 agent 做壞事。** 這和今天的 workspace skill
  沒有差別(使用者本來就能在自己的 workspace 放),hub 只是**擴大了受害範圍**——從自己到
  全站。Q3 明說 AI 審的目的不是保護系統;這條是**接受**的風險,依據是「我們基本上不審」的
  信任模型。`scripts/` 跑在 sandbox 裡,那半沒有新增暴露。
- **AI 審是每次發布一次 LLM 呼叫**,同步在 tool 裡等。發布不是熱路徑,幾秒可接受;模型掛了
  走 Q8。
- **`install_skill` 寫進 workspace 走配額**——和 `materialize_skill` 今天一樣,超額會大聲失敗。
