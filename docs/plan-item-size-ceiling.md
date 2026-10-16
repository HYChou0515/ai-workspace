# Plan — 一個 item 可以調到部署的 `max`，不是只能比預設小

Grilled 2026-10-16 on master `442eca8e`。每條決定標來源：**[user]** = 使用者決定；**[mine]** = 我定的實作細節。

> 這份計畫推翻 [plan-item-sandbox-resources.md](plan-item-sandbox-resources.md) 需求第 3 條「每個 App 可以設定上限，
> 預設就是開上限」所導出的規則——**item 的天花板是 App 的值**（`_resolved_for` 把 item 的值夾在 App 的解析值以下）。
> 那份的其餘部分不變：選的是上限不是預約（§1.1）、只有 CPU 和記憶體（§1.2）、預設 = `min(App 的值, 個人額度)`（§1.3）、
> 活著時唯讀（§1.4）、改大小的權限是 `change_permission`。

## 為什麼

使用者：「我發現我沒辦法調高 sandbox resource」「這個 item 想調高」「App 設定只有 max 和預設，我預設是 1 512m，Max 是 4 4gb」
「我只是要調到 2 2gb」。

部署設了 `resources.per_app.default`（1 核／512M）與 `resources.per_app.max`（4 核／4G）。在 item 的「環境」面板把一個 item
設成 2 核／2G，建出來的 sandbox 仍是 1 核／512M，面板寫「受這個 App 的上限限制」。

機制（master `442eca8e`）：

- `quota/limits.py:resolve_app_limits` 解析每個 App 的大小：app.json 的 `resources` → `per_app.default` → 後端預設。內建的
  4 個 App（playground、pm、rca、topic-hub；`_template` 不會被載入）的 app.json 都沒有宣告 `resources`，所以 App 的值就是 `per_app.default`。
- `per_app.max` 只在開機時拿來檢查 App 的解析值有沒有超過（`_enforce_ceiling`，超過就開機失敗），檢查完就不再被任何東西讀。
- `api/app.py:_resolved_for` 把 item 的值夾在 App 的解析值以下（`_clamp_cpu(cpu, app_cpu)`）。
- 合起來：item 的天花板＝`per_app.default`，`per_app.max` 對 item 沒有作用，「預設」和「上限」被讀成同一件事。

## 決定

| # | 決定 | 來源 |
|---|------|------|
| D1 | **item 的天花板是部署的 `per_app.max`**；App 的值（app.json 的 `resources`，沒寫就是 `per_app.default`）是 item 沒有特別設定時的**預設大小**；owner 的個人額度照舊再夾一層（`min(item 的值, 天花板, 個人額度)`）。設定超過天花板照樣存下，生效時夾到天花板，面板照舊說「受這個 App 的上限限制」。 | [user] |
| D2 | **沒設 `max` 的維度，天花板退回 App 的值**——今天的行為。逐維度判斷：只設了 cpu 的 `max`，就只有 cpu 能往上調。理由：讓沒設 `max` 的部署一升級就沒有上限，是偷偷改掉它們的行為。`max` 寫成 `""`／`"0"`／`"max"` 都當沒設（`parse_size` 本來就把三者讀成同一個「無上限」）。 | [user]；後一句 [mine] |
| D3 | **面板在輸入之前就寫出可調的範圍**：CPU、記憶體欄位下各一行「這個 App 最多 X」，X 就是 D1／D2 的天花板（NN/g 錯誤預防：送出前就說清楚限制，而不是存了才說被壓住）。沒有天花板的維度不顯示那一行，不寫「無上限」。 | [user] |
| D4 | 天花板在 `resolve_app_limits` 算——就是算出 App 大小、檢查 `max` 的同一個地方——存在 `ResourceLimits` 上（`cpu_max`／`memory_max` 欄位與 `item_cpu_ceiling`／`item_memory_ceiling` 兩個屬性）；`_resolved_for` 與環境 API 都讀這兩個屬性，不各自重算。 | [mine] |
| D5 | 環境 API（`GET /a/{slug}/items/{item_id}/environment`）多回 `cpu_ceiling`／`memory_ceiling`（`null` = 沒有天花板），面板用它畫 D3 那一行。 | [mine] |
| D6 | **實際生效的大小已經超過天花板時，不畫 D3 那一行。** 只設 `max`、沒設 `default`、App 也沒宣告的維度，沒人調過的 item 跑的是 sandbox 主機自己的預設（例如 8 核），可能比 `max`（4 核）大；這時「這個 App 最多 4 核」跟畫面上的生效值互相矛盾。大小行為不變（要把沒人調過的 item 也夾到 `max`，得先問主機它的預設是多少，而且會改到 D1 的「預設大小」）。有人設了大小時伺服器已把它夾到天花板以下，碰不到這條。升級手冊提醒設了 `max` 就一併設 `default`。 | [mine]（review 第一輪 F2；照我給使用者的建議先做，使用者尚未回覆） |

不在範圍：**硬碟**——item 層本來就不能調（舊計畫 §1.2），使用者要的是 CPU 和記憶體。

## 影響

- **有設 `max` 的部署，行為會變**：先前在 item 上設了大於 App 值的人，下次開環境就真的拿到那個大小（以前被夾回 App 值）。
  要寫進 `docs/migrations.md`。
- **app.json 宣告 `resources` 的意義變了**：從「這個 App 的 item 最多吃多少」變成「預設吃多少」；最多是部署的 `max`。
  `docs/adding-an-app.md` 與 `docs/configuration.md` 寫著舊規則，要改。目前沒有內建 App 宣告。
- 開機檢查（App 的值超過 `max` 就開機失敗）不變。

## Phases

- **P1** 後端：`ResourceLimits` 帶 `per_app.max`、`_resolved_for` 夾到天花板（D1、D2、D4）；環境 API 回傳天花板（D5）。
  測試先紅：使用者回報的那組設定（default 1／512M、max 4／4G）走真的 `resolve_app_limits`，item 設 2／2G 要建出 2／2G。
- **P2** 前端：環境面板在 CPU、記憶體欄位下寫出天花板（D3）；沒有天花板就不顯示。
- **P3** 文件：本計畫、舊計畫標題下的推翻標記、`docs/design-history.md`、`docs/adding-an-app.md`、`docs/configuration.md` 的規則
  說明、`docs/migrations.md` 條目。
- **P4** review 第一輪：記憶體「無上限」不畫「最多 0 B」；config 的 `cpu` 寫成 `"max"`／`"0"`／`""` 讀成「沒設」、其他非數字開機失敗；D6；升級手冊補會變小的方向、滾動更新、在跑的環境的計費；掃掉仍寫舊規則的註解。
