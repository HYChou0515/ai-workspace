# `datetimerange` — 一個已經在說謊的 role 名稱

`span` 的值在 #785/#789 之後已經是**小時粒度**,role 卻仍叫 `daterange`。使用者讀
`.entity/issue/schema.yaml` 時被這個名字誤導。

這包**只改名字**,不改任何行為。時間模型、拉桿、非工時摺疊、排程都維持現狀。

---

## 1. 現狀(已查證)

### 1.1 粒度不是 role 的性質,是**每一個邊各自**的性質

`ganttScale.ts:263` 的 `canonicalEdge` 把純日期保留成 `YYYY-MM-DD`、把帶時刻的保留成
`YYYY-MM-DDTHH:mm`;`ganttScale.ts:295` 的 `instantOf` 再把**純日期的結束邊**當成「到隔天午夜
為止」。所以在同一個 role 底下:

| 值 | 意思 |
|---|---|
| `2026-07-13/2026-07-15` | 三天的工作(inclusive) |
| `2026-07-13T09:30/2026-07-13T12:00` | 一個上午 |
| `2026-07-13/2026-07-15T12:00` | 混用,合法 |

這是重要的:**沒有「純日期的 range」和「帶時刻的 range」兩種東西**,只有一種 range,它的邊可以
粗可以細。所以正解不是新增第二個 role,是把唯一那個 role 改名成誠實的上位名。

### 1.2 其餘現況

| 事實 | 位置 |
|---|---|
| role 是封閉詞彙 `StrEnum`,`DATERANGE = "daterange"` | `entity/schema.py:27` |
| schema 是從**item 自己的 workspace** 讀的,不是從 app 套件 | `entity/catalog.py:119` |
| 未知 role **不會**丟掉整個 entity type:該欄位降級成 `text`,留一則 **warning** | `entity/catalog.py:66-72` |
| `seed_item` 只在**建立 item 時**呼叫一次,沒有任何 re-seed / 同步路徑 | `apps/seeding.py:55`、唯一呼叫點 `api/item_routes.py:342` |
| widget 名是後端從 role **推導**的,使用者從不撰寫、不存在任何檔案裡 | `entity/forms.py:28,51` |
| 前端**不解析** `schema.yaml`,只吃後端正規化過的 role | `web/src` 全域 grep 無命中 |
| PM 的兩個 seed 檔寫著 `role: daterange` | `apps/pm/profiles/default/.entity/{issue:22,milestone:11}/schema.yaml` |

### 1.3 天真改名的失敗模式:**看起來像功能沒了,不像壞掉**

因為 §1.2 第三列,沒有相容拼法的話,既有 item 的 `span` 會安靜地變成一個**文字欄位** ——
甘特圖那根 bar 消失、表格變成可以亂打的字串、值還好端端躺在檔案裡。而它只留 warning。

CI 照不到:entity 相關測試的 schema 幾乎都是當場組出來的 `FieldSpec`,不是走
`.entity/schema.yaml` 那條路;走那條路的 `tests/entity/test_catalog.py:114` 也是自己寫
schema 位元組,不會用到既有 item 的檔案。**沒有任何測試持有「上一版種出來的檔案」。**

---

## 2. 鎖定的決策

| 決策 | 內容 |
|---|---|
| **正名 `datetimerange`** | 誠實的上位名。`timerange` 會讓人以為沒有日期;沿用 `span` 是把 PM 的欄位名當成通用 role 名 |
| **舊拼法永久接受,不是過渡期** | `.entity/schema.yaml` 已經是**使用者的檔案**(§1.2 第四列),我們不回頭改寫它 |
| **alias 只存在於一個地方** | `Role._missing_` 把 `"daterange"` 映到 `DATETIMERANGE`。enum 的 `.value` 就是 `"datetimerange"`,**下游看不到舊拼法存在** |
| **不改寫既有 item 的檔案** | 見 §3 |
| **widget 名一起改,且不需要相容** | 它是推導出來的,沒有任何歷史資料帶著它。留著只會讓同一個誤導的字在程式碼裡多活一份 |
| **零行為改變** | 值的格式、解析、繪製、排程一律不動。這包只換字串 |

### 為什麼這不是「兩套規則並存」

「一條規則兩個地方」的危險在於**兩份實作會漂移**。這裡是**一份實作、一張同義字表**:alias 在
`Role` 的解析點把舊拼法收斂掉,`forms.py` 的 widget 表、`brief.py` 的分支、API 回給前端的 role、
每個 view kind 都只看得到 `datetimerange`。要讓它退化成「兩套」,必須有人在解析點以外再判斷一次
字串 —— 那是 review 看得見的東西。

---

## 3. 為什麼不改寫既有 item 的檔案

1. **那是使用者的檔案。** `seed_item` 種完就不再管;使用者(或 agent)可能已經改過它 —— 加欄位、
   改 status 值。回頭重寫會撞到他的修改。
2. **改寫也擋不住下一次。** 使用者、agent、任何外部範本都能再寫出 `daterange`。需要的是「平台看得
   懂這個拼法」,不是「這一批檔案被洗過一次」。
3. **成本不對稱。** alias 是一個 `_missing_`;migration 是一次寫進所有人 workspace 的批次作業,
   而它換到的只有「舊檔案裡那個字也變好看」。

**已知代價,不迴避:** 既有 item 的 `schema.yaml` 會一直寫著 `daterange`,所以打開舊 item 的 schema
**仍然會看到那個誤導的字**。這包改的是平台的正名與新 item 種出來的字,不是把歷史洗乾淨。若之後要
連舊檔案一起改,那是獨立一步,要單獨決定。

---

## 4. Phases

### Phase 1 — `Role` 正名 + 舊拼法 alias

`entity/schema.py`:`DATERANGE = "daterange"` → `DATETIMERANGE = "datetimerange"`,並加
`_missing_` 讓 `Role("daterange")` 回傳 `DATETIMERANGE`。

**測試(先紅)**
- `Role("daterange") is Role.DATETIMERANGE`,且 `.value == "datetimerange"` ——
  舊拼法進得來、出去只有一個名字。
- 走**真實路徑**:餵一份 `role: daterange` 的 `schema.yaml` bytes 給 `catalog` 的 build,斷言
  該欄位的 role 是 `DATETIMERANGE` 且**沒有** warning diagnostic。這條是 §1.3 那個失敗模式的
  守衛 —— 拿掉 `_missing_` 它必須紅。
- 未知 role(例如 `role: bogus`)仍然降級成 `text` 並留 warning:alias **只**赦免那一個拼法,
  不是把錯字也吞掉。

### Phase 2 — 後端其餘位置

- `entity/forms.py:28` widget 表 → `"datetimerange"`。
- `entity/brief.py:36` 分支跟著 enum 名改。
- PM 兩個 seed schema(`issue/schema.yaml:22`、`milestone/schema.yaml:11`)→ `role: datetimerange`。

**測試** — `test_forms.py` 既有那條「`daterange` 拿到 `daterange` widget」改成新名字;
另補一條:**用舊拼法寫的 schema,推導出來的 widget 也是新名字**(證明正規化真的發生在解析點,
而不是靠 seed 檔剛好是新的)。

### Phase 3 — 前端字串

`web/src/api/entities.ts:41` 的 role union、`roleWidget.tsx:28,40,274,339`、
`EntityViews.tsx:86`、`sortRows.ts:52`。註解裡的 `daterange` 一併更新
(`GanttView.tsx:5`、`ganttScale.ts:5,150,172,350`、`shared.tsx:318`、`TableView.tsx:435`、
`schedule.ts:63`、`roleWidget.tsx:7,140`)。

**測試** — 既有 FE 測試把 fixture 的 `role: "daterange"` 換成新名字即可;**不**加新測試,因為前端
從來看不到舊拼法(§1.2 第六列),替它寫一條「前端也接受舊拼法」的測試會是**假的守衛** —— 它保護
的路徑不存在。

### Phase 4 — 文件

`docs/plan-issue-785.md` §5 那條「不新增 `datetime` role」的非目標要標註**已被本計畫取代**,並說明
取代的理由不同:它拒絕的是「多一個 role」,本計畫做的是「唯一那個 role 改名」,分支數不變。
其餘 plan 檔(`plan-issue-419.md`、`plan-issue-448.md`、`plan-pm-auto-schedule.md`、
`plan-pm-entity-body-edit.md`)提到 role 詞彙的地方一併更新。

---

## 5. 非目標

- **不動 `schedule` 的天粒度。** #785 §5 記錄的理由(排程正確性有自己一組測試要重寫)今天仍然成立,
  而且跟命名無關。
- **不動 `date` role(`due`)。** 它是**單一時點**不是區間,名字沒有說謊。它要不要吃時刻是另一個
  獨立決定。
- **不新增第二個 role。** 見 §1.1:只有一種 range。
- **不改寫既有 item 的檔案。** 見 §3。
- **不改任何行為。** 值格式、解析、繪製、拖曳吸附、非工時摺疊全部不動。

---

## 6. 風險

### 6.1 唯一真正的風險是「alias 沒有被走到」

失敗模式不是報錯,是既有 item 的 span 安靜變成文字欄位(§1.3)。守衛必須走**真實路徑**
(schema bytes → catalog → FieldSpec),不能只斷言 `Role("daterange")` 這個純函式 —— 那條路上
還有 `catalog.py:66` 的 `try/except ValueError`,而 `_missing_` 若回傳 `None` 它一樣會吞掉。
Phase 1 第二條測試就是為這個而寫。

### 6.2 `StrEnum` + `_missing_` 的細節

`Role` 是 `StrEnum`,`_missing_` 必須回傳成員而不是字串,否則 `Role("daterange")` 會拋
`ValueError` 而被 `catalog.py:67` 接住 —— 症狀跟完全沒做一模一樣。這條要有測試釘住,**而且要對
沒改的版本驗紅**。

### 6.3 msgspec 反序列化不一定走 `_missing_`

`FieldSpec` 是 msgspec struct,但 `catalog.py:66` 是**手動**呼叫 `Role(...)` 之後才組
`FieldSpec`,所以走的是 Python enum 的路徑。若之後有人改成讓 msgspec 直接解 `Role`,alias 會
靜默失效。→ Phase 1 的 catalog 測試同時是這件事的守衛。
