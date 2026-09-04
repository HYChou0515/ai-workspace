# #785 — PM app 七項:時間軸拉到小時、非工時摺疊,以及一張現在會說謊的甘特圖

> 盤點基準:`origin/master` @ `ad21f4df`。
> 七項需求,但不是七件獨立的事:第 1、2 項共用同一個機制,第 7 項順帶修掉一個現在就在說謊的地方,第 5 項有硬證據支持直接刪除。

---

## 1. 現狀(已查證)

### 1.1 gantt 的時間模型比想像中乾淨

整張圖 —— 軸、bar、拖曳、today 線 —— 都只透過三個函式看時間:

```
columnOf(from, date, skip)     一個日期是第幾欄
dateAtColumn(minDate, col, …)  第幾欄是哪一天(逆函式)
barColumns(span, skip)         一段 span 佔幾欄
```

`GanttView.tsx:263` 的註解說得很清楚:「every position is a COLUMN offset (working days when on, calendar days when off) via columnOf, so the whole gantt — axis, bars, drag, today — counts only working days」。

**這是整包最重要的事實。** 「精確到小時」不是換一個時間模型,是把「欄 = 一天」推廣成粒度可變;而「跳過非工時」跟「跳過週末」是同一個機制的兩個粒度,不是第二套東西。

呼叫端只有兩處:`GanttView.tsx`(5 個位置)與 `ganttScale.ts` 內部的 band 計算。範圍可控。

### 1.2 其餘現況

| 事實 | 位置 |
|---|---|
| `span` 的 role 是 `daterange`,值是 `YYYY-MM-DD`;**平台沒有 datetime role** | `issue/schema.yaml:22`、`roleWidget.tsx:23` |
| `parseSpan` 用 `Date.parse` 回傳 **epoch ms** —— 解析層其實已經吃得下時間 | `shared.tsx:248` |
| 但 `ganttScale.ts` 的 `Span` 是 `{ start: string; end: string }`,全靠 `YYYY-MM-DD` 可字典序比較 | `ganttScale.ts:13` |
| `workload.ai.yaml` 和 `gantt.ai.yaml` 是**同一個 `view: gantt`**,只差 `group_by` | 兩個 YAML |
| View settings **已經能切 `group_by`**,並有「本地改 → dirty 圓點 → Save to view 寫回 YAML」 | `ViewSettingsPanel.tsx:63,235` |
| 沒有 span 的 record 被**直接 `filter` 掉** | `GanttView.tsx:214` |
| milestone 的 `span.start` 是其 issues 的**下界**,`end` 由 schedule 算出 | `milestone/schema.yaml` 註解 |
| `initialIdeCollapsed` 只讓 `primary_surface: "chat"` 預設收起;PM 是 `"views"` | `WorkspaceShell.tsx:94` |
| 只有兩個 app 設了 `primary_surface`(rca=ide、pm=views) | `apps/*/app.json` |

### 1.3 一個既有缺陷:圖在說謊

`barColumns` 有 `Never below 1` 的保底:

```ts
export function barColumns(span: Span, skip: boolean): number {
  return columnOf(span.start, span.end, skip) + 1;
}
```

於是在 `skip_weekends: true` 下,一個「週六 → 週日」的 issue:`columnOf` 回傳 0(兩天都摺疊到同一欄),`barColumns` 回傳 **1**。

它佔整整一欄,看起來跟一個正常的一天任務一樣寬,而且跟週一的工作**疊在同一欄**。圖把「完全沒有工作時間」畫成「一個工作日」。

第 7 項的窄線順帶修掉它 —— 這不只是新功能,是把一個假象改掉。

---

## 2. 鎖定的決策

| 決策 | 內容 |
|---|---|
| **粒度跟拉桿連動** | 不是新模式,是把既有 zoom 軸**往細的方向延長**。拉過某個密度,欄自動從「天」切到「小時」。**預設不變** —— fit-to-pane 仍是天,既有專案打開來跟今天一模一樣 |
| **純日期 = `00:00–23:59`** | 資料存**日曆真值**,不在儲存時偷改使用者填的東西;摺疊是顯示層的事。因為非工時零寬度,`00:00` 和 `07:00` 在畫面上本來就同一個 x |
| **非工時 = 週末的日內版** | 共用 `columnOf`。天粒度下「一天 = 一欄」本來就等於一個工作日,小時粒度才展開成 14 欄。**不寫第二套摺疊邏輯** |
| **拖曳吸附到小時** | 「span 到分鐘、chart 到小時」——分鐘留給手動輸入 |
| **開關走既有機制** | skip-weekend 與工時視窗都進 View settings,沿用 dirty → 「Save to view」。不發明新的偏好儲存 |
| **Roadmap 聯集只改顯示** | 不寫回 `milestone.span`(理由見 §3) |
| **畫不出來的東西不准消失** | 兩種形態:**實心窄線** = 有時間但被摺疊;**虛線 + 半透明** = 系統推導的,你還沒設 |

### 顯示語彙

| 狀態 | 畫成 |
|---|---|
| 正常 span | 實心 bar |
| 整段落在非工時 / 週末 | **實心窄線**,像被壓在摺疊縫裡 |
| 只設了單邊 | 推導另一邊(±1 週),**虛線 + 半透明** |
| 兩邊都沒設 | 今天起算一週,**虛線 + 半透明** |

實心與虛線的分野是「這是你設的」對「這是我猜的」,而寬度的分野是「它在時間軸上有多少工作時間」。兩個維度獨立。

---

## 3. 為什麼 Roadmap 的聯集只能改顯示

一旦寫回 `milestone.span`,下界會被自己的 issue 往前侵蝕:

```
拖早一個 issue → milestone.start 變早 → 下次 Recalculate 拿更早的 start 當下界 → 又能排更早
```

每按一次 Recalculate 漂一次,而且**沒有任何一步看起來是錯的** —— 每一步都只是「照著資料算」。顯示層做聯集就沒有這個回饋迴圈:圖說實話,而你設的下界還是你設的。

---

## 4. Phases

### P1 — 版面:最便宜、每天都看得到的三件事

**Goal.** gantt 成為第一個 tab、PM 的檔案樹預設收起、workload 刪除。

- `app.json` 的 `layout.views` 重排,`/views/gantt.ai.yaml` 移到最前。
- 刪 `workload.ai.yaml`,並從 `layout.views` 移除。**理由是查證過的**:View settings 已能切 `group_by`,所以它就是「Timeline 預設選 assignee」,使用者在 Timeline 裡切一下就得到同一張圖。
- 新的純函式 `initialSidebarState(primary_surface, isNarrow)`:`views` → `closed`,其餘寬螢幕 → `pinned`,narrow 一律 `closed`(#464 既有規則)。只影響 PM(唯一的 views app),不動沒設定的 app —— 不是 hardcode slug。

**「左側」是檔案樹,不是 IDE —— 實作時查證後推翻了原本的寫法。** 原計畫寫的是改 `initialIdeCollapsed`,那是錯的:`EditorArea` 整個包在 `!ideCollapsed` 裡,而 views-first app 的 gantt 正是開在 `EditorArea` 當 tab,所以把 IDE 收起會**連甘特圖一起藏掉**,和需求 4 完全相反(既有測試 `opens the workspace up front for a views-first App (#419 §B5)` 也正好守著這件事,維持綠)。真正該收的是 `sidebarState` 那條 260px 的檔案樹;50px 的 activity rail 留著,所以是收起不是移除,點 Files 或 ⌘B 就回來。

**判準要裝在值被算出來的地方。** `sidebarState` 的 `useState` 初值會被 `useEffect(…, [isNarrow])` 在 mount 當下蓋掉,只改初值等於加了一個靜默失效的死旋鈕。所以兩個寫入點共用同一個 `initialSidebarState`,且 effect 的相依是抽出來的字串而非 manifest 物件(refetch 換了物件 identity 會把使用者剛開的樹關掉)。突變探針驗過:把 effect 改回 `isNarrow ? "closed" : "pinned"`,行為測試紅、純函式測試全綠 —— 單靠單元測試看不見這件事。

**驗收.** 開 PM 直接看到 Timeline 且檔案樹收起;Timeline 的 View settings 切 assignee 得到原本 Workload 的畫面。

### P2 — 時間值升級(零行為改變)

**Goal.** `daterange` 的值可以帶時間,純日期讀成 `00:00–23:59`。**純函式層,先不接進畫面。**

- `ganttScale.ts` 目前把 span 當 `YYYY-MM-DD` 字串**字典序比較**。升級成一個明確的時刻型別(epoch ms 或帶時間的 ISO),並保留「純日期 → 當天 00:00 / 23:59」的正規化。
- `parseSpan` 已經回傳 epoch ms,所以真正要動的是 `ganttScale` 這一側的字串假設。
- 這個 phase **不改變任何畫面**:所有 span 都是純日期時,新舊行為必須逐像素相同。

**驗收.** 既有 gantt 測試全綠且未修改斷言;新增的正規化測試涵蓋「純日期」「帶時間」「單邊」三種輸入。

### P3 — 欄粒度可變 + 拉桿延長到小時

**Goal.** `columnOf` / `dateAtColumn` / `barColumns` 接受粒度;拉桿拉過閾值切到小時。

- 三個函式的簽名多一個粒度參數(`day` | `hour`)。呼叫端只有 `GanttView.tsx` 與 `ganttScale.ts` 的 band 計算。
- `PPD_MAX` 提高,並定義切換閾值:密度超過某個 px/day 時,欄改為小時。
- **預設不變**:`fitPpd`(fit-to-pane)仍落在天粒度。

**驗收.** 不碰拉桿時,每一個既有 gantt 測試不改斷言仍綠;拉到最細時軸顯示小時。

### P4 — 非工時摺疊

**Goal.** 工時視窗(預設 07:00–21:00),非工時零寬度,與 `skip_weekends` 共用同一條路。

- `columnOf` 的 skip 從一個 boolean 推廣成一份「什麼時間不算數」的規則:週末 + 每日的非工時視窗。
- 天粒度下無感(一天仍是一欄);小時粒度下一天展開成 14 欄。

**驗收.** 天粒度下的所有既有測試不變;小時粒度下,跨夜的 bar 不佔非工時寬度。

### P5 — 開關進 View settings

**Goal.** skip-weekend 從 YAML 布林值變成面板上的 switch;工時視窗掛同一處。

- YAML 仍是持久化的家(`skip_weekends` 這個 key 不改名),面板只是它的編輯介面。

**需求 3 在 master 上就已經做完了。** `ViewSettingsPanel` 的「Working days」區塊早就有 `Skip weekends (Mon–Fri only)` 這個 checkbox,經 `onToggleSkipWeekends` → `setViewScalar` 寫回 YAML。所以這個 phase 實際只剩「把工時視窗掛到同一個區塊」。

**寫回機制原本寫錯了。** 原文說「本地改 → dirty 圓點 → Save to view」,但那是**非 gantt** 分支的機制;gantt 的齒輪是 `dirty: false` + `persistGantt` **立即寫回**,而且刻意做成針對單行的 comment-safe 文字編輯,而不是 `saveView` 的 js-yaml dump —— 否則那些自我說明的 `week:` 註解區塊會被整個吃掉。既然是「沿用既有的」,就沿用 gantt 這一套,不為單一控制項發明第二套。

**做的時候撞到一個會讓整個 view 變空白的缺陷。** `setViewScalar` 只換掉 `key:` 那一行,而 `work_hours` 是縮排的 block —— 改寫會留下兩個孤兒子鍵、YAML 解析失敗、`parseViewSpec` 回 `null`,結果不是「設定沒生效」而是**整張圖不見**。已改成 block-aware(取到第一個空行或退回同縮排為止),`sort:` 之類未來寫成 block 的 key 也一併受惠。

**驗收.** 面板上切「Skip non-working hours」→ 出貨的 `gantt.ai.yaml` 真的多出可被解析器讀回的 `work_hours`,且 `week:`/`schedule:`/`skip_weekends` 全部存活(有測試讀真檔驗證)。

### P6 — 畫出畫不出來的

**Goal.** 沒有 span 的 issue 進得了畫面;被摺疊到零寬度的 bar 看得見。

- 移除 `GanttView.tsx:214` 的 `filter`,改成推導 span:單邊補另一邊(±1 週)、兩邊都沒有就今天起算一週。
- 推導出來的畫成**虛線 + 半透明**。
- `barColumns` 的 `Never below 1` 保底改掉:真的零工作時間就是零欄,畫成**實心窄線**而不是撐成一欄。**這會改變既有畫面**(週末任務從「一欄寬」變成窄線)——那正是 §1.3 要修的假象。
- 拖曳一根推導的 bar,等於把推導變成使用者設定的值。

**驗收.** 一個沒有 span 的 issue 出現在它該在的 group lane;一個週六→週日的 issue 畫成窄線而非整欄;拖它之後 span 被寫回且樣式從虛線變實心。

### P7 — Roadmap 聯集

**Goal.** milestone 的 bar 涵蓋 `milestone.span ∪ 其 issues 的 span`,**只在顯示層**。

- milestone schema 已有 `issues: { role: backref, from: issue.milestone }`,所以資料關係存在;要確認 backref 在 gantt 的 render 路徑上取得到。
- 不呼叫 `onPatch`,不寫回 `milestone.span`。

**驗收.** 手動把一個 issue 拖到其 milestone 開始之前,roadmap 的 bar 跟著變長;重新載入後 `milestone.span` 的檔案內容**未改變**。

---

## 5. 非目標

- **`schedule` 自動排程維持「天」粒度**(`exp_days` 仍是天)。它算出來的 span 是整天,使用者再手動細調。把排程一起改成小時會讓這包變成兩包,而且排程的正確性有自己的一組測試要重寫。
- **不新增 `datetime` role**。`daterange` 的值升級成可帶時間,role 名稱不變 —— 多一個 role 就多一個要在每個 widget、每個 view kind、每個 editor 裡處理的分支,而 `daterange` 只有 PM 在用。
- **不做「每人不同工時」**。工時視窗是 view 層的一份設定,不是 per-assignee 的日曆。
- **不碰 `due`(`role: date`)**。它是單一時點,跟這包的區間問題無關。

---

## 6. 風險

### 6.1 `monthBands` 有無窮迴圈的前科,而 P3/P4 正好動它的輸入

`ganttScale.ts` 的註解記著:週末原點曾讓 `columnOf` 和 `dateAtColumn` 差一,`monthBands` 把它變成零寬度 band 和**無窮迴圈 —— 任何專案都會凍住分頁**。

P3 讓粒度可變、P4 讓「跳過的東西」從整天變成一天之內的區段,兩者都直接改變這對函式的一致性前提。**這對函式必須永遠互為逆函式**,而且要有明確覆蓋「摺疊區間的原點」的測試,否則同一個凍結會回來。

### 6.2 P6 會改變既有畫面,而且是刻意的

週末任務從「一欄寬」變成窄線,是**視覺回歸**還是**修正**,取決於有沒有人把現在的寬度當成真的。plan 的立場是後者(§1.3),但這要在 PR 裡明說,不能讓它看起來像不小心改壞。

### 6.3 純日期讀成 `00:00–23:59` 之後,`end` 的 inclusive 語意要保住

`barColumns` 的註解寫著:「a `daterange` is inclusive: 7/13–7/15 is a three-day task, not a two-day one」。升級成時刻之後,`7/15` 是 `7/15 23:59` 而不是 `7/15 00:00`,inclusive 才守得住。這是 P2 唯一容易寫錯的地方,而寫錯的症狀是**每根 bar 都短一天**。

### 6.4 拉桿延長之後,最細端的欄數會暴增

一年在天粒度是 365 欄,在小時粒度(14h/天)是 5110 欄。若渲染是「每欄一個 DOM 節點」,拉到最細會產生數千個節點。需要確認軸的 band 計算是**依可視範圍**產生而非整個資料範圍 —— `visibleDaysFor` 的存在暗示已經是,但 P3 要驗證而不是假設。

---

## 7. 這些決定是怎麼來的

Grill 的過程有三個轉折點,每一個都推翻了我原本的推薦。記在這裡,因為 plan 只寫「決定是什麼」的話,下一個人會以為那是唯一想得到的答案,然後在某次重構裡把它改回去。

### 7.1 純日期該讀成什麼 —— 我的推薦被否決,而且該被否決

我推薦把純日期讀成**工作日的兩端**(`07:00` / `21:00`),理由是既有 bar 的長度不變、新輸入不必填時間、而且 bar 永遠落在工時內。

否決的理由更好:**應該讀成 `00:00–23:59`。**

差別在於「誰負責摺疊」。我的版本把摺疊**寫進了資料** —— 使用者填 `2026-01-05`,存進去卻變成 `07:00`,那是在儲存時偷改他填的東西,而且一旦工時視窗改成 `09–18`,舊資料就全錯了。正確的分層是資料存**日曆真值**,摺疊完全交給顯示層。

而顯示結果**完全相同**:非工時零寬度,所以 `00:00` 和 `07:00` 在畫面上本來就是同一個 x。同樣的畫面,乾淨得多的語意。

### 7.2 畫不出來的東西怎麼畫 —— 不要發明新語彙

我推薦給「整段落在非工時」的 bar 一個**空心虛線的佔位標記**,跟沒有 span 的用同一套。

否決的理由:**它就是一根被壓扁的 bar,畫成一條窄線就好** ——「像是沒有寬度的長條,看起來就像被壓在裡面」。

這比我的好,因為它不引入新語彙。同一根 bar 的極限狀態,顏色、點擊、拖曳全都保留;使用者不需要學一個新符號,他看到的就是「這件事被擠到沒有空間」。而我的版本會讓兩件不同的事(被摺疊 vs 沒設定)長得一樣。

最後的語彙因此變成兩個**獨立**維度:實心/虛線分「你設的 vs 我猜的」,寬度分「有多少工作時間」。

### 7.3 粒度怎麼決定 —— 這一項讓整包變便宜

我原本把第 1 項理解成「把欄從天換成小時」,並據此估成一個會動到整條鏈的大改動。

更正是:**粒度跟 timeline 的拉桿連動,預設不變,只是現在可以拉更細。**

這不是同一件事。它把「換一個時間模型」變成「把既有的 zoom 軸往細的方向延長」——既有專案打開來一模一樣,只有主動拉細的人才會遇到小時粒度。風險、測試量、以及「會不會弄壞別人現在的畫面」全都降一個等級。

順帶讓 §2 的「非工時 = 週末的日內版」自洽:天粒度下「一天 = 一欄」本來就等於一個工作日,不需要為它寫任何特例。

### 7.4 兩項根本不必討論,因為 code 已經回答了

- **刪 workload**:它是 `view: gantt` + `group_by: assignee`,而 View settings 早就能切 `group_by`。刪掉不損失任何能力 —— 這是查出來的,不是判斷出來的。
- **switch 放哪**:面板已有「本地改 → dirty 圓點 → Save to view」的完整機制。照用,不發明。

Grill 的規矩是「能查的就去查,不要拿去問人」,這兩項是它省下的兩輪問答。
