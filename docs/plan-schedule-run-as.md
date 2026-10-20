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
| G3 | 認不出作者的(seed、zip 匯入、複製、exec 寫的、上線前就在的):**未指定**,只用 shared + service account。升級後既有排程不變 | 〔user〕 |
| G4 | 有 `execute` 的人都能「用我的身分執行」;取代別人先確認、事後通知原本的人(沿用 #869) | 〔user〕+既有 |
| G5 | **superuser**(`server.superusers`)可以指定別人;被指定的人必須在該 item 有 `execute`,否則 422 | 〔user〕+〔預設〕 |
| G6 | 被指定的人直接生效、收到通知、隨時「停止用我的身分」→ 未指定 | 〔user〕 |
| G7 | 作者沒有 `execute` → 不綁(未指定);之後失去權限 → 沿用 sweep 的解綁+通知 | 〔預設〕 |
| G8 | 排程列顯示「用 {誰} 的身分」+ 為什麼:本人選的／排程作者／管理員 {by} 指定／未指定;**不逐一列變數** | 〔user〕 |
| G9 | 總表和 Workflows 面板用**同一組元件與字**(NN/g *Consistency and Standards*:「use the same patterns everywhere」);兩邊不同時以總表為準;WUI 彈窗的身分那行也用同一個元件 | 〔user〕+〔預設〕 |
| G10 | Workflows 面板列這個 item 的排程(含頁面的),資料就是總表篩到這個 item——即 index 登記過的每一份,外加 item 自己那份還沒登記的(D1) | 〔user〕 |
| G11 | app／profile 宣告的時間觸發、entity 事件觸發:不做,另開 issue | 〔user〕 |

NN/g 的另一個依據:*Visibility of System Status* ——「no action with consequences to users
should be taken without informing them」。用某人的身分跑是對他有後果的動作,所以 G6 通知、
G8 每一列都寫出來。

## 機制

### 「誰寫的」:只在知道是誰的門上認,用前後差集

facade 的 `_landed` 不知道是誰(`files/facade.py:500`,只有 workspace_id 和 path),exec 寫的檔
從 mirror 進來時也沒有人。所以**不在 facade 認**,而在「請求者是誰很清楚」的門上認:

| 門 | 寫的人 | 位置 |
|---|---|---|
| AI 工具 `save_schedules`、`write_file`、`edit_file`(後兩者是 AI 寫**頁面**排程的唯一辦法;review 第 1 輪補上) | 這一輪代表的人(`acting_for`):聊天 = 訊息作者(goal 續跑／重跑 = 原作者);workflow 節點 = run 的 `env_user`(按的人／綁定的人,沒有就是空) | `agent/tools.py` |
| 檔案 PUT——編輯器存檔、WUI 頁面 `writeFile`、檔案樹上傳、Workflows 面板「匯入」都走它 | 請求者 | `api/file_routes.py` |
| 排程「改時間」 | 請求者 | `api/schedule_listing.py` |

做法:門在寫之前讀舊檔,寫之後拿新內容,`新 key − 舊 key` 的每一列,若**還沒有綁定**且作者
有 `execute` 且它的 workflow 解析得了,就綁給作者(`reason="author"`,digest 照 #869 的同意規則)。

**為什麼是差集,不是「沒有綁定的 key 都算新的」**:`save_schedules` 每次存整份檔。若用「沒綁定
=新」,AI 為了加一列重存整份,會把升級前就在、或有人「停止用我的身分」的舊列全部認到自己名下
——G3 和 G6 同時破功。差集只認這次真的新出現的列。

其他任何讓一列出現的方式(exec／shell、複製、搬移、zip 匯入、seed、workflow 的 shell 步驟、
檔案「尋找取代」`POST …/replace`、workflow run 的檔案上傳暫存)都不經過這些門 → 未指定(G3)。
不需要 backfill:升級前的列從來不會出現在差集裡。

三條護欄(review 第 1 輪起):寫之前那份檔**可能藏了 sweep 讀不到的列** → 這次什麼都不認,因為「修好」不等於「新寫」——
怎樣算「可能藏了列」是一張偏向安全方向的表(`schedule_bindings._unreadable` 的 docstring;第 2–6 輪逐步補齊):
空白、或只有空結構、或乾淨的 `schedules` 清單(旁邊只有空的或純量的值)照常認,其他有內容的、重複 key 的一律不認;寫之後那份**超過列數上限** → 什麼都不認(sweep 不會觸發它,也不會在超量時清綁定);
認作者這一步**失敗了只記 log**,不讓已經寫好的檔回報失敗。

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
| P5 | 前端共用元件;總表加「執行身分」欄;面板改列全部排程、同一組字;WUI 彈窗身分行用同一元件;改時間的說明改成「改的人的身分」 | `web/src/components/schedules/`、`SchedulesOverviewPage.tsx`、`WorkflowsModal.tsx`、`PageIdentity.tsx`、`ScheduleTimeModal.tsx`、`lib/i18n.tsx` |
| P6 | 文件:`docs/migrations.md`、舊計畫標推翻、`design-history.md`、`extending-the-platform.md`;skill(`author-workflow`、`wui` 與其 `reference.md`)與 `save_schedules` 回覆說出用誰的身分 | `docs/`、`sample-skills/author-workflow/`、`sample-skills/wui/` |

## 偏離

| # | 計畫 | 實際 | 理由 |
|---|---|---|---|
| D1 | 面板 = 總表篩到這個 item | 另外把 item 自己的 `.workflows/schedules.json` 列進來,**即使 index 還沒登記**,回應多 `unindexed` | 總表只列 index 登記過的檔(sweep 會動的);面板以前一直列這份檔並說「還沒被登記」。照搬總表會讓這種排程從面板消失 |
| D2 | grill 時給 user 看的表寫「作者沒有 `execute` → 排程列寫明原因」(上面 G7 已照實作改寫成只有「不綁」) | 不綁,顯示成「未指定」,**不另寫原因** | 原因要另存一列「沒人但有理由」的綁定,會讓「沒有綁定=沒人」這條 sweep 也在用的規則多一種例外;畫面上的「未指定」已經是真的 |
| D3 | 兩邊用字不同時以總表為準 | 面板的「下次:…」「下一輪掃描(…)」、自己的移除確認句、執行後只寫「已開始執行」全改成總表的(「下一次」欄、「馬上就會執行」、帶位置的確認句、「已開始執行 · 查看」);舊 i18n key 刪除 | G9 |
| D4 | 列表帶 `runs_as`/`reason`/`by` | 多一個 `runs_as_mine`,由伺服器判斷 | 頁面彈窗的資料本來就由伺服器給 `mine`;兩邊都由伺服器說「是不是你」,前端不自己比對 user id |
| D5 | (未提) | 舊的 `GET …/schedules`(面板原本讀的)保留 | 前端已不用,但它和 sweep 的對照測試(`test_schedules_route_parity.py`)守著「列表=sweep 會做的事」;拆掉是另一件事 |
| D6 | WUI 頁面彈窗用同一個元件 | 用了同一個 `RunsAs`;但彈窗的資料沒有 `can_assign`,所以彈窗不給「指定…」,照舊對每個人顯示「用我的身分執行」(沒有 `execute` 的人按了會被伺服器拒絕並顯示原因,和改版前一樣) | superuser 從總表或面板指定;彈窗是看頁面的人用的 |
| D7 | (未提) | 作者綁定(`reason="author"`)的那一列被改掉或刪掉時,sweep 收掉綁定**不發通知**;「自己選的」「管理員指定的」照舊通知——但在同一份檔裡**仍綁著某一列**的人不通知(典型:他自己改了時間,新的那列算他的) | 作者從沒按過「用我的身分執行」,舊通知叫他「再按一次」是錯的;自己改時間的人,只要他仍能執行且 workflow 解析得了,新的那列就是他的。workflow 檔被改時照舊通知所有人(原因 `workflow_changed`,通知講 workflow 被改了)——那一列還在,只是悄悄回到沒人 |
| D8 | (未提) | superuser 指定的人**就是目前綁定的人** → 什麼都不變(不改原因、不重取同意、不通知) | review 第 1 輪:人員選單裡點已勾選的那個人,會把本人自己選的改標成「管理員指定」並用現在的 workflow 重取同意 |

## Review 第 1 輪(四鏡頭)

修了:寫之前檔案壞掉／寫之後超過上限不認(兩條護欄,見機制);`write_file`／`edit_file` 也認作者;
認作者失敗不讓存檔失敗;面板只讀這個 item 的 index 與頁面、index 或綁定表讀不到時降級而非 500;
作者綁定被改掉時不發錯的通知(D7);重新指定同一人不變(D8);移除／綁定被拒後清單照樣重讀;
列以 `trigger_id` 為 key(位置會讓「已開始執行」跑到別列);改時間的說明對沒有 `execute` 的人改說「只用共用的值」;
`save_schedules` 沒接作者認定時不聲稱「runs as」;一串文件措辭(匯入、rollout、G10「全部」)。

**已知限制(記錄,不修):**

- **頁面載入就寫 `schedules.json`,會綁到看頁面的人。** WUI 頁的 `writeFile` 走檔案 PUT,寫的人就是正在看頁面的
  那個人;能改頁面的人可以寫一頁「一打開就存排程」的頁面,讓有 `execute` 的觀看者的值被綁上去,而觀看者只會在
  排程列上看到「用你的身分執行(排程是你寫的)」。這是 G2(誰寫的就是誰)的直接結果,動到 user 定的規則,
  所以沒有自行改;要擋就得區分「人按的存檔」和「頁面自己存的」,或在建立作者綁定時另外通知——待 user 決定。
- `credit` 先讀綁定再寫(非原子):同一瞬間有人按「用我的身分執行」,可能被作者綁定蓋掉而不通知;機率極低。
- 「改時間」用的是讀進來的那份檔,寫之前才重讀「舊檔」:兩者之間被別人刪掉的列,會被算成這次新增的(算改的人的)。

## Review 第 2 輪(四鏡頭,兩個 agent)

修了:**空檔、`{}`、`{"schedules": null}` 算「沒有列」**,不是「解析不了」——檔案樹「新增檔案」先寫空字串,
第 1 輪的護欄讓編輯器的第一次存檔不認作者(判準是一張表、偏向「讀不了」:空白、或整份只有空結構(`{}`、`[]`、`null`、`{"schedules": null}`、`{"schedule": []}`…)
→ 沒有列,照常認;乾淨的 `schedules` 清單(其他 key 都是空的)→ 照差集認;**其他任何有內容的**——不是 JSON、單獨一列、
key 打錯、清單旁邊另有內容、`schedules` 不是清單——一律不認。判錯只會錯成「這次沒人」,那一列的人自己按「用我的身分執行」就好;
反方向會把別人的列送給下一個存檔的人。第 2–6 輪 review 都抓到上一版判錯的形狀,這張表是它們的總和);AI 工具與「改時間」在寫之前／之後
的讀檔失敗時只是不認、寫入照常(第 1 輪只修了檔案 PUT);自己改了自己那一列不收到「已不再用你的身分」(D7);
workflow 被改而收綁定的通知講 workflow(新原因 `workflow_changed`);面板「匯入」先寫 workflow 檔、最後寫
`schedules.json`(否則 workflow 還不存在,認不了);改時間的說明補上「workflow 解析不了時例外」;runbook rollout 措辭。

查過不改:移除／綁定被拒後清單重讀,列的 `trigger_id` 變了會重新掛載、列內的錯誤字消失——但這些 mutation 都沒有設
`silentError`,原因照樣經過全域的寫入失敗提示(`api/queryClient.ts` 的 `MutationCache.onError` → `reportWriteFailure`)。

**已知限制(追加):** 「同一份檔裡仍綁著某一列的人不通知」是按檔案判斷,不是按那一列——舊列和新列連不起來;
所以一個人在同一份檔綁了兩列、其中一列被別人改掉時,他不會收到通知(那一列的「執行身分」照實顯示)。

## Review 第 3、4 輪

第 3 輪(單一 agent、四鏡頭,審第 2 輪的修正):寫之前那份檔是合法 JSON、但不是排程清單(key 打錯、裸清單)時,
第 2 輪的放寬讓修檔的人拿走裡面的列——第 1 輪的 bug 回來了。修成 `_unreadable`。

第 4 輪(審第 3 輪的修正):`_unreadable` 把「一列都沒有」的 `[]`、`{"schedule": []}`、`null` 也當成壞掉,
之後真的寫入列的人不被認作者,而測試把這個錯的答案釘住了。判準改成「JSON 裡有非空的清單或物件、又不在
`schedules` 清單裡」才算藏了列(`_holds_a_row`),測試換成真的藏了一列的形狀。

第 5 輪(審第 4 輪的修正):第 4 輪的判準看漏了「單獨一列、沒有外層」與「合法清單旁邊另有一列」,兩者都往危險方向錯
(把列送給修檔的人)。同一個判準已經第三次換寫法,所以改成先列表、偏向安全方向(見機制的三條護欄),不再追求每種
奇怪形狀都判「對」:有內容但不是乾淨排程檔的,一律不認。

第 6 輪(審第 5 輪的表):重複的 key——JSON 只留最後一個值,前一個裡的列 sweep 和這個檢查都看不到——往危險方向錯;
解析時遇到重複 key 就算讀不了(`object_pairs_hook`)。另外把清單旁邊的**純量**(例如頁面蓋上的 `"version": 1`)
算成沒有藏東西:數字或字串修好了也不會變成一列,原本會讓那份檔之後每次存檔都不認作者。
