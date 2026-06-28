# 新增一個 App

這個平台是 **multi-App(多 App)**(#89)。一個 **App** 就是程式碼裡
`src/workspace_app/apps/<slug>/` 底下的一個目錄。在那裡丟進一個新目錄,就會產生一個平行、
獨立品牌的 dashboard——launcher 卡片、item 清單、create 流程、workspace
shell、agent——全部由該 App 的 `app.json` + model 驅動。RCA(`apps/rca/`)只是
其中一個 App;scaffold(腳手架)`apps/_template/` 則是一份「複製我」的起點。

註冊是開機時對 `apps/` 的一次**掃描**(`apps/registry.py`):任何含有
`app.json` + `model.py` 的目錄都會被探索、註冊,並顯示在
launcher 上。**沒有需要編輯的中央清單。**(像 `_template` 這種 `_` 開頭的目錄會被
略過——它們是內部用的,不對使用者公開。)

## 快速開始

1. 複製 scaffold:
   ```
   cp -r src/workspace_app/apps/_template src/workspace_app/apps/<your-slug>
   ```
   目錄名稱**就是** slug,所以它必須是合法的 Python package 名稱
   (小寫、不能有連字號):`tickets`、`audits`、`incidents`。
2. 編輯 `<your-slug>/app.json`——把 `slug` 設成與目錄相符,然後填入
   identity / agent / item / layout / lifecycle(參考下方)。
3. 編輯 `<your-slug>/model.py`——把 `TemplateItem` 及其 enum 改名成你的
   領域;把 `INDEXED_FIELDS` 設成你用來 filter / sort / 上色的欄位。
4. 編輯 `<your-slug>/prompts/system.md`——agent 的 base prompt。
5. 編輯 `<your-slug>/profiles/default/`——create 流程會 seed 的起始內容
   (再多加幾個 profile 就能提供可挑選的多樣選項)。
6. 開機(`uv run python -m workspace_app`)。App 會出現在 launcher 上;不需要動
   任何其他檔案。

## 各個檔案

```
apps/<slug>/
├── app.json                     # identity、agent 上限、layout、lifecycle、各種開關
├── model.py                     # WorkItem Struct（MODEL + INDEXED_FIELDS）
├── prompts/
│   └── system.md                # agent 的 base system prompt
└── profiles/
    └── default/                 # 一份起始內容 bundle（create 流程的預設）
        ├── _prompt.md           # 附加在這個 profile 的 system prompt 之後
        ├── _profile.json        # （選用）收窄 tools/presets、suggestions
        ├── .skill/<name>/SKILL.md  # （選用）可被 read_skill 載入的 skill
        └── *.tpl                # seed 進 item 的檔案（$title/$owner/… 會被代入）
```

## `app.json` 參考

| 欄位 | 意義 |
|---|---|
| `slug` | App id——**必須等於目錄名稱** |
| `title` / `description` | launcher 卡片文字 |
| `icon` | `flame`(具名)、一個 emoji,或 `icon.svg`(同層檔案,內嵌) |
| `color` | 一個 hex → App 的 `--accent` 三色組(App 內整套重新配色) |
| `function.workspace` | file IDE(tree + editor + file tools)。`false` → 只有 chat 的 shell |
| `function.sandbox` | exec + package tools。不需要 terminal;控制 exec 相關功能的開啟 |
| `function.terminal` | 人用的 shell 分頁。**需要 `sandbox: true`** |
| `agent.prompt_file` | base system prompt 的路徑(相對於 App 目錄) |
| `agent.tools` | App 的 tool **上限**;profile 可以收窄成其子集 |
| `agent.picker` | `[{preset, name}]`——model picker;`preset` ∈ `config.yaml` 的 `agents.presets` |
| `agent.suggestions` | App 層級的 quick-prompt chips(profile 可覆寫) |
| `item.{noun,noun_plural,create_label}` | 給人看的字串(「Start Investigation」) |
| `layout.{breadcrumb,statusbar,list,form}` | 每個 surface 上顯示哪些領域欄位 |
| `layout.default_tabs` | workspace 進場時開啟的檔案(只篩出有 seed 的那些) |
| `lifecycle` | `{status_field, closing_states}`——驅動 Close 功能 |
| `labels` | 各欄位的顯示 label |
| `field_styles` | enum option → tone token(`err`/`warn`/`ok`/`info`/`muted`)——把 chip 顏色當作資料 |
| `default_profile` | 使用者沒挑選時,create 流程 seed 的 profile |

**開關的一致性在開機時強制檢查**(`validate_function_coherence`):例如
`agent.tools` 裡有 `exec` 卻 `sandbox: false`,或 `terminal: true` 卻
`sandbox: false`,都會讓開機大聲失敗。`_template` App 出貨時帶
`sandbox: false` + 只有 file 的 tools,用來示範一個 workspace-only(無 sandbox)的 App。

## `model.py` 合約

匯出 `MODEL`(一個 `WorkItemBase` 子類)+ `INDEXED_FIELDS`:

- **Tier 1**(從 `WorkItemBase` 免費取得):`title`、`owner`、`description`、
  `profile`、`attached_preset`。
- **Tier 2**(opt-in):`members`、`topics`——如果你的 App 有用到,就重新宣告成具體的 `list[str]`。
- **Tier 3**:你自己的型別化領域欄位(enum / scalar)。把它們標好型別讓它們
  原生建索引——把你用來 filter / sort / 上色的那些列進 `INDEXED_FIELDS`。

欄位的 **kind + enum options** 會從 model 投射進 manifest
(`GET /apps/{slug}.fields`),所以 FE 不需要重述型別就能 render + inline-edit 它們——
`enum → select`、`str → text`。

## Profiles

一個 profile 就是一份起始內容 bundle。`default` 是必要的;多出貨幾個就能給 create
流程一個 **profile picker**(當數量 >1 時才會出現)。每個 profile:

- `*.tpl` 檔 → 在 create 時 seed 進 item,並把 `$title` / `$owner` /
  你的 Tier-3 欄位代入(`.tpl` 後綴會被去掉)。
- `_prompt.md` → 附加在這個 profile 的 system prompt 之後。
- `_profile.json`(選用,`apps.profiles.ProfileManifest`):`title`、
  `description`、`suggestions`、`tools`(⊆ `agent.tools`)、`presets`
  (⊆ `agent.picker`)、`default_preset`。省略則繼承 App 的完整上限。
- `.skill/<name>/SKILL.md`(選用):可被 `read_skill` 載入的 skill,帶
  `name` + `description` frontmatter;當 profile 有出貨任何 skill 時,agent 會拿到一份
  「## Available skills」索引 + `read_skill` tool。

## Presets

`agent.picker` 用名稱參照 **presets**;presets 住在 `config.yaml` 的
`agents.presets` 底下(model + creds + sandbox image + idle timeout)。沿用
bundled 的 `qwen3-local` / `claude-opus` / `openai-mini`,或自己加。

## 限制

- 目錄名稱就是 slug——一個合法的 Python package 名稱(registry 會 import
  `apps.<slug>.model`)。不能有連字號。
- `_` 開頭的目錄不會被探索(拿來放 scaffold / 內部 helper)。
- 資料**不會**跨 App 共用——每個 App 有自己的 resource table。
