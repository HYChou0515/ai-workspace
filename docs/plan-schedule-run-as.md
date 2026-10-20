# 排程用誰的身分執行：預設是寫它的人，superuser 可指定，兩個畫面看得到

> grill 2026-10-20。每條決定標了來源:**〔user〕** = user 逐題定案;
> **〔預設〕** = 我自己定、列出來給推翻的實作層決定。

## 問題

`plan-wui-viewer-login.md`(#869)讓排程可以「用我的身分執行」——但按鈕只畫在 WUI 頁面的
平台列／工具列(`PageIdentity.tsx`),而且只讀「那個頁面資料夾/schedules.json」。所以:

1. AI 用 `save_schedules` 存的 `.workflows/schedules.json`、排程總表 `/schedules`、item 的
   Workflows 面板,**沒有任何地方按得到**。這些排程永遠「沒人綁」:只拿 shared + 部署的
   `env_without_request`,私人值(只有我／我的環境變數)一個都拿不到,`private_only` 的變數必缺。
2. 誰的身分完全看不到:總表和面板都沒有這一欄(只在 WUI 小彈窗)。
3. 同一個排程在總表、面板、WUI 彈窗長三個樣子(14 處差異,欄位、用字、確認句都不同)。

後端其實已經接受任何一份 `schedules.json` 的綁定(`schedule_binding_routes.py:58`),
sweep 對每份檔都查綁定(`user_schedule_sweep.py:633`)——缺的是「預設是誰」和入口。

## 決定

| # | 決定 | 來源 |
|---|---|---|
| G1 | 範圍:`schedules.json`(`.workflows/` 和每個頁面資料夾的) | 〔user〕 |
| G2 | **誰寫的排程,預設就用誰的身分**。入口表見下 | 〔user〕+表〔預設〕 |
| G3 | 認不出作者的(seed、匯入、複製、exec 寫的、上線前就在的):**未指定**,只用 shared + service account。升級後既有排程不變 | 〔user〕 |
| G4 | 有 `execute` 的人都能「用我的身分執行」;取代別人先確認、事後通知原本的人(沿用 #869) | 〔user〕+既有 |
| G5 | **superuser**(`server.superusers`)可以指定別人;被指定的人必須在該 item 有 `execute`,否則 422 | 〔user〕+〔預設〕 |
| G6 | 被指定的人直接生效、收到通知、隨時「停止用我的身分」→ 未指定 | 〔user〕 |
| G7 | 作者沒有 `execute` → 不綁(未指定);之後失去權限 → 沿用 sweep 的解綁+通知 | 〔預設〕 |
| G8 | 排程列顯示「用 {誰} 的身分」+ 為什麼:本人選的／排程作者／管理員 {by} 指定／未指定;**不逐一列變數** | 〔user〕 |
| G9 | 總表和 Workflows 面板用**同一組元件與字**(NN/g *Consistency and Standards*:「use the same patterns everywhere」);兩邊不同時以總表為準;WUI 彈窗的身分那行也用同一個元件 | 〔user〕+〔預設〕 |
| G10 | Workflows 面板列這個 item 的**所有**排程(含頁面的),資料就是總表篩到這個 item | 〔user〕 |
| G11 | app／profile 宣告的時間觸發、entity 事件觸發:不做,另開 issue | 〔user〕 |

NN/g 的另一個依據:*Visibility of System Status* ——「no action with consequences to users
should be taken without informing them」。用某人的身分跑是對他有後果的動作,所以 G6 通知、
G8 每一列都寫出來。

## 機制

### 「誰寫的」:只在知道是誰的門上認,用前後差集

facade 的 `_landed` 不知道是誰(`files/facade.py:500`,只有 workspace_id 和 path),exec 寫的檔
從 mirror 進來時也沒有人。所以**不在 facade 認**,而在三扇「請求者是誰很清楚」的門上認:

| 門 | 寫的人 | 位置 |
|---|---|---|
| `save_schedules` 工具 | 這一輪代表的人:聊天 = 訊息作者(goal 續跑／重跑 = 原作者);workflow 節點 = run 的 `env_user`(按的人／綁定的人,沒有就是空) | `agent/tools.py` |
| 檔案 PUT(編輯器、WUI 頁面 `writeFile`) | 請求者 | `api/file_routes.py` |
| 排程「改時間」 | 請求者 | `api/schedule_listing.py` |

做法:門在寫之前讀舊檔,寫之後拿新內容,`新 key − 舊 key` 的每一列,若**還沒有綁定**且作者
有 `execute` 且它的 workflow 解析得了,就綁給作者(`reason="author"`,digest 照 #869 的同意規則)。

**為什麼是差集,不是「沒有綁定的 key 都算新的」**:`save_schedules` 每次存整份檔。若用「沒綁定
=新」,AI 為了加一列重存整份,會把升級前就在、或有人「停止用我的身分」的舊列全部認到自己名下
——G3 和 G6 同時破功。差集只認這次真的新出現的列。

其他任何讓一列出現的方式(exec／shell、複製、搬移、zip 匯入、seed、workflow 的 shell 步驟)
都不經過這三扇門 → 未指定(G3)。不需要 backfill:升級前的列從來不會出現在差集裡。

一列被改(時間、`run`、`with`)= key 變 = 新的一列 = 改的人的(G2),舊 key 的綁定由既有的
`expire_absent` 收掉。

### 綁定多兩個欄位

`ScheduleBinding.reason: "self" | "author" | "admin"`(預設 `"self"` = 既有列)、
`assigned_by`(admin 時是哪個 superuser)。sweep 不看這兩欄——身分照舊是 `user_id`。

### superuser 指定別人

`PUT …/schedule-bindings/{key}?path=&user=<id>`:`user` 不是呼叫者自己 → 呼叫者必須是 superuser
(403),目標要在 item 上有 `execute`(422)。通知被指定的人;取代掉的人照舊通知。

### 列表:兩個畫面同一份資料

- `OverviewRow` 加 `runs_as` / `runs_as_reason` / `runs_as_by`;`ScheduleOverview` 加
  `can_assign`(呼叫者是不是 superuser)。綁定一個 item 一次查(`for_item`)。
- 新路由 `GET /a/{slug}/items/{id}/schedules/overview`:同一個組裝函式,篩到這個 item——
  Workflows 面板改用它,列出頁面排程也就順理成章(G10)。
- 前端把總表的格子(下一次、上一次、執行身分、動作)抽成共用元件,面板用同一組,排成直的。

## Phase

| P | 內容 | 主要檔案 |
|---|---|---|
| P1 | `ScheduleBinding.reason/assigned_by`;`bind()` 記下來;「用我的身分執行」記 `self` | `workflow/schedule_bindings.py`、`api/schedule_binding_routes.py` |
| P2 | 作者認定 `credit_new_rows`(差集、未綁、`execute`、解析得了);接到三扇門;`AgentToolContext.acting_for` 由 chat / workflow builder 填 | `workflow/schedule_bindings.py`、`agent/tools.py`、`api/turn_context.py`、`api/workflow_exec.py`、`api/file_routes.py`、`api/schedule_listing.py` |
| P3 | superuser 指定別人 + 通知被指定的人 | `api/schedule_binding_routes.py` |
| P4 | 列表帶身分;`can_assign`;per-item overview 路由 | `api/schedule_listing.py` |
| P5 | 前端共用元件;總表加「執行身分」欄;面板改列全部排程、同一組字;WUI 彈窗身分行用同一元件;改時間的說明改成「改的人的身分」 | `web/src/components/schedules/`、`SchedulesOverviewPage.tsx`、`WorkflowsModal.tsx`、`PageIdentity.tsx`、`ScheduleTimeModal.tsx` |
| P6 | 文件:`docs/migrations.md`、舊計畫標推翻、`design-history.md`、`author-workflow` skill 與 `save_schedules` 回覆說出用誰的身分 | `docs/`、`src/workspace_app/skills/` |

## 偏離

| # | 計畫 | 實際 | 理由 |
|---|---|---|---|
| D1 | 面板 = 總表篩到這個 item | 另外把 item 自己的 `.workflows/schedules.json` 列進來,**即使 index 還沒登記**,回應多 `unindexed` | 總表只列 index 登記過的檔(sweep 會動的);面板以前一直列這份檔並說「還沒被登記」。照搬總表會讓這種排程從面板消失 |
| D2 | G7「作者沒有 `execute` → 排程列寫明原因」 | 不綁,顯示成「未指定」,**不另寫原因** | 原因要另存一列「沒人但有理由」的綁定,會讓「沒有綁定=沒人」這條 sweep 也在用的規則多一種例外;畫面上的「未指定」已經是真的 |
| D3 | 兩邊用字不同時以總表為準 | 面板的「下次:…」「下一輪掃描(…)」、自己的移除確認句、執行後只寫「已開始執行」全改成總表的(「下一次」欄、「馬上就會執行」、帶位置的確認句、「已開始執行 · 查看」);舊 i18n key 刪除 | G9 |
| D4 | 列表帶 `runs_as`/`reason`/`by` | 多一個 `runs_as_mine`,由伺服器判斷 | 頁面彈窗的資料本來就由伺服器給 `mine`;兩邊都由伺服器說「是不是你」,前端不自己比對 user id |
| D5 | (未提) | 舊的 `GET …/schedules`(面板原本讀的)保留 | 前端已不用,但它和 sweep 的對照測試(`test_schedules_route_parity.py`)守著「列表=sweep 會做的事」;拆掉是另一件事 |
| D6 | WUI 頁面彈窗用同一個元件 | 用了同一個 `RunsAs`;但彈窗的資料沒有 `can_assign`,所以彈窗不給「指定…」,照舊對每個人顯示「用我的身分執行」(沒有 `execute` 的人按了會被伺服器拒絕並顯示原因,和改版前一樣) | superuser 從總表或面板指定;彈窗是看頁面的人用的 |
