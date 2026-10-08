# 第三方工具同名 command:模型看到的名稱帶本地名前綴

**狀態:** grill 完成(2026-10-08),待施工。
**來源標記:** 〔user〕= user 的原話或明確選擇;〔查證〕= 讀 `origin/master` 程式碼確認的事實;〔施工〕= 我定的實作細節,可推翻。

## 1. 問題〔查證〕

兩個第三方工具(#674)都匯出同名 command 時(例:本地名 `a`、`b` 都有 `list-files`),那個 App 的
聊天 turn 失敗,錯誤是 `cross-package tool name collision: command 'list-files' appears in packages ['a', 'b']`。

機制:

- 模型看到的 tool 名稱 = command 名稱本身(`tooling/registry.py` `_to_function_tool`:`name=cmd.name`)。
- 組 turn 的 tool 清單時 `build_function_tools` → `_check_collisions`:兩個**不同套件**有同名 command 就
  `raise ValueError`,整份清單組不起來。WUI 的 `callTool` 走 `find_allowed_command`,同一個檢查,回 409。
- 原設計(`plan-skills-and-tools.md` T5)是「撞名就請部署方改名」。第三方工具由不同作者各自上架,
  誰都不知道別人的 command 叫什麼,這個前提不成立。
- 授權語法本來就分得開:app.json / item 開關 / workflow 節點 / sub-agent 都寫 `pkg` 或 `pkg:cmd`。
  撞名的只有「模型與頁面怎麼稱呼它」這一層(模型 tool 名、WUI `tools:` / `callTool`、skill 內文、
  聊天紀錄的 `tool_name`、工具卡片 `flat_catalog`)。
- 第三方工具的套件名 = 運營方在 `app.json` `agent.external_tools` 取的本地名(`tooling/external.py`),
  在一個 App 內唯一,不受別人上架什麼影響。

## 2. 決定

| # | 決定 | 來源 |
|---|---|---|
| N1 | **第三方工具一律帶前綴**:模型看到 `<本地名>__<command>`(例 `a__list-files`)。自家打包的工具(第一方套件)維持扁平名稱。 | 〔user〕「A」 |
| N2 | **舊的扁平名稱當別名,不影響現有**:模型或頁面用 `list-files` 呼叫,這個 App 裡只有一個套件有它就照常轉過去;撞名時那一次呼叫回一句話列出新名字(「`list-files` 有兩個:`a__list-files`、`b__list-files`,請指名一個」),不讓整個 turn 失敗。舊聊天紀錄的卡片照常顯示。 | 〔user〕「A 但不要影響現有」「就是你的建議」 |
| N3 | **分隔符用雙底線 `__`**(供應商的 tool 名稱只能用英數、`_`、`-`,不能用冒號)。頁面與授權語法另外也接受 `a:list-files`,兩種寫法指同一個 command;文件教 `a__list-files`。 | 〔user〕「Ok 用雙底線吧」 |

## 3. 施工時我定的事〔施工〕

| # | 定了什麼 | 為什麼 |
|---|---|---|
| D1 | 名稱檢查:組出來的名字必須符合 `^[A-Za-z0-9_-]{1,64}$`;不符合的那個 command 在開機時寫 log(App、本地名、command)並不提供給模型,其他照常。 | 目前完全沒有這個檢查,不合規的名稱會讓供應商拒絕整個請求。 |
| D2 | 第一方套件之間撞名仍在開機時報錯。 | 第一方的名字由我們控制;撞了是我們的錯,要大聲。 |
| D3 | 別名解析只看**這個 turn / 這個頁面被授權**的 command,和模型 tool 清單同一份展開(`_select_commands`)。 | 別名不能叫到沒被授權的東西。 |
| D4 | 工具卡片(`flat_catalog`)新名、舊名都查得到;撞名的舊名不顯示成任一個。 | 舊聊天紀錄的卡片照常;不猜它是哪一個。 |
| D5 | MCP runner 不動:它一次只服務一個套件,不會撞名。 | |
| D6 | 第三方 command 與內建 tool 同名(例 `read_file`)時,前綴後就不再相撞;頁面用扁平名 `read_file` 指的是內建,不轉到套件。 | 和模型那側(`dedupe_tools`:內建優先)同一條規則。 |

## 4. Phases

| Phase | 內容 |
|---|---|
| P1 | registry:第三方套件的 FunctionTool 名稱 = `<本地名>__<cmd>`;`_check_collisions` 只對第一方之間報錯;D1 名稱檢查 |
| P2 | 模型用扁平名(或 `a:cmd`)呼叫時的別名:唯一 → 轉過去;撞名 → 回一句話列出新名字;turn 不失敗 |
| P3 | WUI `callTool` / `tools:` 接受 `a__cmd`、`a:cmd`、不撞名的扁平名;撞名回 409 帶同一句話;D6 |
| P4 | 工具卡片與選單(D4);系統提示的 tool 清單用新名字 |
| P5 | 文件:`sample-skills/wui` 範例與 reference 教新名字;`docs/migrations.md` 一條;`plan-skills-and-tools.md` T5 標推翻 |
| P6 | review 回合、CI、PR |

## 5. 不做

- 不改授權語法(`pkg` / `pkg:cmd`)。
- 不改第一方工具的名稱。
- 不改舊聊天紀錄存的 `tool_name`。
