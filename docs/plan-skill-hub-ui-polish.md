# Plan — skill hub 的收尾：demo 錄影與你的 review 抓到的 24 條，一支 PR

**狀態：** 計畫，等點頭（2026-09-19）。來源是 #818 合併後錄的九支 /web-demo、你看片時提的六條、以及 7 個介面 × 6 種視窗的小尺寸量測。以前端為主；後端的兩條（工具回話的換行、給人看的伺服器句子）**也一起**——它們不是機制問題，一條是分段、一條是照抄站內既有的「code + 參數、前端翻譯」形狀。#822（技能面板一列的 cluster 換行）**併進來**，那條 draft PR 關掉。

每一項都對照一個成熟設計系統的做法，出處寫在決策表；圖示一律用現成 icon set 的 glyph，不手畫。

## 為什麼

Skill hub 的功能都在（#818），但看片時它「像個工程師做的」：對話框沒留白、圖示看不懂、同一個錯報兩次、搜尋打字會失焦、狀態只用一顆多出來的圖示表示。這些沒有一條需要新設計——每一條都有教科書答案，缺的是照著做。

## 決策表（全部，含出處）

| # | 現況 | 改成 | 依據 |
|---|---|---|---|
| **D1** 對話框留白 | `ModalShell` 面板本身沒有 padding，靠 28 個呼叫者各自傳 `panelStyle`（0／18／20／24 都有）；5 個沒傳的（含我寫的轉移 owner、開新 item 兩個）就零內距 | `ModalShell` 面板**預設 `padding: 20`**（現有呼叫者最常用的值）；有傳 `panelStyle.padding` 的照舊（含 3 個刻意 `0` 的）。整類消失，不是補兩個 | [Material 3 Dialogs](https://m3.material.io/components/dialogs)：內容區 24dp、標題到內文 20dp |
| **D2** 搜尋失焦 | `SkillHubPage.tsx:71` `if (isPending \|\| !data) return <p>載入中…</p>`——每個新 (q, mine) 查詢鍵都 pending，整頁（含搜尋框）被換掉，輸入框重掛、焦點與游標都掉；「我的／全部」也閃載入 | 查詢用 `placeholderData: keepPreviousData`；**搜尋框與切換鈕永遠在**，載入狀態只畫在結果區（第一次載入才顯示載入文字） | 輸入框不可因結果更新而卸載——即時過濾的基本要求；TanStack 的 `keepPreviousData` 正是為此 |
| **D3** 錯誤報兩次 | picker 再裝一次被拒：picker 內已顯示伺服器那句，全域 `MutationCache.onError` 又跳「儲存失敗，內容未套用」toast | 自己就地顯示錯誤的 mutation 標 `meta: { silentError: true }`（`queryClient.ts` 設計好的退出點）：picker 的 install、條目頁的 unpublish/republish/permission/transfer/delete/edit（它們有 `skillHub.failed` 就地列） | [NN/g Indicators, Validations, and Notifications](https://www.nngroup.com/articles/indicators-validations-notifications/)：toast 不拿來報錯；錯誤就地、持續 |
| **D4** 「有新版」看不見 | 副本有新版時列上只多出第四顆沒標籤的圖示（↻）；提示與 tooltip 寫「出貨版本」（package 的措辭） | 列上加文字 badge「有新版」；更新鈕的 tooltip 與更新後提示依來源分句：package 副本「更新為出貨版本／已更新到出貨版本」、hub 副本「更新為 hub 上的新版／已更新到 hub 上的新版」 | [Polaris Badge](https://polaris.shopify.com/patterns/new-features)：狀態用 badge；[NN/g Icon Usability](https://www.nngroup.com/articles/icon-usability/)：通用圖示極少 |
| **D5** ↺ 手畫 SVG | `Icon name="undo"` 的路徑看不出是 ↺ | 換成 icon set 的 `undo` glyph（和現有 ↓ ↑ 同一套，`Icon.tsx` 裡的 path 改掉，不加文字——你的要求） | NN/g Icon Usability |
| **D6** picker 文案 | 「裝進這個 item」；intro「別人發布的 skill，裝進這個 item 就能用。提到這個 App 沒有的工具的，會先說。 到 skill hub 看全部」；缺 tool 句「提到 X，這個 App 沒有——裝了也可能有步驟做不到。」 | 按鈕「安裝」；intro 一句「裝別人發布的 skill 進這個 item；缺這個 App 沒有的 tool 時會先告訴你。」＋獨立一行連結「到 skill hub 看全部 →」；缺 tool 句「缺少 tool：X，可能有步驟無法執行」 | [Apple HIG Alerts/Buttons](https://developers.apple.com/design/human-interface-guidelines/components/presentation/alerts/)：按鈕以動詞開頭；警示句先講缺什麼再講後果 |
| **D7** 「有審查意見」tag | 只在 verdict=notes 時出現；你認為每條都審過、這個區分不該存在 | 列表上拿掉這個 badge；條目頁的「AI 審查意見」區照舊 | Polaris：badge 表示狀態、明講別到處貼資訊 badge |
| **D8** picker 不標「已裝」 | 要按下去才知道被擋 | 拒絕的條件是「這個 item 已有同名資料夾」，picker 用**同一個條件**提前標示：條目名和 `GET …/skills` 列裡的任一 skill 名相同 → 列上標「已有同名 skill」、安裝鈕 disabled。不能標「已安裝」——`/skills` 的 `upstream` 只有 state 與 update_available、沒有 entry id，同名可能是使用者自己寫的 | 把已知資訊放在決策點；判準和拒絕同一條（不是另一套會漂的規則） |
| **D9** 「我的」看不出 fork | 在「我的」視角 fork 變成自己的 root，卡片上沒來源 | fork 卡片在任何視角都多一行「fork 自 alice/csv-peek」（原作讀不到就寫「fork 自一個已下架／已刪除的條目」，沿用條目頁的 lineage 資料） | 同 D8 |
| **D10** 轉移後直接跳走 | 轉移「已下架」的條目給別人 → 條目變別人的私有 → 頁面被 navigate 回列表，沒有任何訊息 | 轉移成功後帶著 router state 回列表，列表頂端畫一個可關閉的 success notice：「已把 log-digest 轉給 Bob Liu。它現在是 Bob 的私有條目，你看不到了。」新元件 `PageNotice`（success 樣式）放在 `/skill-hub` 頁；刪除成功也用它（現有 `skillHub.deleted` 文案） | [GOV.UK notification banner (success)](https://design-system.service.gov.uk/components/notification-banner/)；Material：非錯誤的確認用 snackbar/banner |
| **D11** 開新 item 沒帶 skill | 新 item 表單上沒有任何關於這個 skill 的提示 | AppNewItem 收 `?skill=<entry id>` 時，表單頂端一行「建好後，到技能面板把 default-user/csv-peek 裝進來再修改」；不自動裝（建 item 是表單、裝是面板，兩步各自可見） | 同 D8 |
| **D12** 可見範圍對話框 | 共用 `PermissionDialog`：標題 Share “…”、選項 Private/Restricted/Public 是英文，Public 說明「Everyone in the workspace」對 hub 語意錯 | `PermissionDialog` 的標題、三個選項與說明改走 i18n（zh-TW／en），並多一個 `audience` 參數：hub 傳「平台上所有人」，item 傳既有的「這個 workspace 的所有人」；其他呼叫者行為不變（預設 = 今天的字） | 共用元件的文案要可參數化；混語是缺陷 |
| **D13** 窄寬 | picker 390px：安裝鈕占半寬、說明擠成窄欄；技能面板底部 390px：提示折三行擠在按鈕旁；面板列（#822） | picker 列與面板列同一套：文字欄 `flex: 1 1 200px`、控制項成一個 cluster 隨列換行；底部提示在容器 < 480px 時隱藏（`@container` 或 `useHeaderTier` 同款量測，不用視窗寬度常數） | #822 的量測法（Chromium 1280／390，`getClientRects().length`） |
| **D15** 工具回話黏 bullet | `publish_skill` 的回話用單一換行接句子（`agent/tools.py:2610` `"\n".join(lines)`）；轉述時 markdown 把清單後的「It mentions these tools…」「It is public…」併進最後一則審查建議 | 段與段之間用空行（`"\n\n".join`）；清單自成一段。同檔另兩處 `"\n".join`（`:1439` 標題+清單、`:2217` ask_user）先看是不是同一種形狀，是就一併改 | markdown 的清單規則；工具回話是模型與人都會讀的文字 |
| **D16** 給人看的伺服器句子是英文 | skill hub 路由回 `detail` 字串（「this workspace already has alice's '.skill/x/'…」「only the owner may manage this entry」「no such skill hub entry」「transfer needs a different, non-empty owner」「bob already publishes a skill named …」），前端原樣顯示，混進中文介面 | 照站內既有形狀：`detail` 改成 **`{code, …params}`**，前端用 i18n 翻（配額錯誤 `turn_gate.py`→`quotaFailure.ts`、以及 hub 自己的「修改」路由 `reason: closed/deleted/no_access`→`skillHub.edit.reason.*` 都已是這樣）。五個 code：`not_found` / `owner_only` / `transfer_owner_required` / `transfer_name_taken(owner,name)` / `folder_in_the_way(owner,path)`。`skill_folder_in_the_way` 改回**事實**（whose/path 的 struct），兩扇門各自成句：tool 給模型的英文句照舊，路由給前端 code。未知 code 的後備：狀態碼 + 通用句 | 站內先例（配額 code、edit reason）；判準在算出來的地方、句子在讀者那邊 |
| **D17** 面板列的四顆圖示鈕（P5 做完後你看了截圖：「看不懂 2–4 是什麼意思」「更新和還原 icon 看不出差別」「發布看起來是上傳」） | ↓ 下載、↑ 發布（和底部「匯入」同一顆 `upload` glyph）、↺ 還原、↻ 更新——還原與更新只是鏡像；四顆只有三顆有 tooltip | 圖示換成 icon set 裡各自專用的 glyph：**發布 = 雲＋上箭頭**（Material `cloud_upload`／Lucide `cloud-upload`，「送到共用的地方」，和匯入的 ↑ 分開）；**還原 = 時鐘＋逆時針箭頭**（Material `restore`／Lucide `history`，「回到之前的狀態」）；更新維持 ↻（Material `refresh`）；下載不變。字**不放列上**（你說的：已經很擠）——四顆都有 tooltip（`title`，站內的慣用法；Material 3：icon button 一律配 tooltip）＋ aria-label。`undo` glyph 照 D5 留給另外三個「復原」用途 | [NN/g Icon Usability](https://www.nngroup.com/articles/icon-usability/)：圖示要能互相分辨、要有文字（tooltip 是最低限度）；[Material 3 Icon buttons](https://m3.material.io/components/icon-buttons/guidelines)：配 tooltip |
| **D14** 頂欄 390px | 「切換」折兩行、麵包屑截成「回…」「Skill h…」（共用 chrome） | 「切換」在窄寬只留圖示（有 aria-label）；麵包屑保留**最後一段**完整、前段收成「…」（GOV.UK breadcrumbs 的 collapse-on-mobile 做法） | [GOV.UK Breadcrumbs](https://design-system.service.gov.uk/components/breadcrumbs/) |

## 不做（這支 PR 之外）

- **模型讀的工具回話改語言**：tool 回給模型的英文句照舊（真模型會用使用者的語言轉述），D16 只改路由直接給人看的那五句。
- 新 API：D8 用既有 `/skills` 的 skill 名比對、D9 用既有 lineage 欄位、D11 用 query string；D16 改的是既有路由的 `detail` 形狀（唯一的客戶端是這個前端）。

## 形狀

- `web/src/components/ModalShell.tsx`：預設 padding（D1）。
- `web/src/pages/SkillHubPage.tsx`：keepPreviousData、結果區載入、拿掉 badge、fork 來源行、`PageNotice`（D2、D7、D9、D10）。
- `web/src/components/PageNotice.tsx`（新，success/info 兩種，可關閉，`role="status"`）。
- `web/src/pages/SkillHubEntryPage.tsx`：mutations 標 silentError、轉移／刪除後帶 state 回列表、修改→開新 item 帶 `?skill=`（D3、D10、D11）。
- `web/src/pages/AppNewItem.tsx`：讀 `?skill=` 畫提示（D11）。
- `web/src/components/SkillHubPickerModal.tsx`：文案、已安裝、窄寬 cluster、silentError（D3、D6、D8、D13）。
- `web/src/components/SkillsModal.tsx`：#822 的 cluster + 「有新版」badge + 依來源分句 + 底部提示隱藏（D4、D13）。
- `web/src/components/Icon.tsx`：`undo` glyph（D5）；新增 `publish`（雲＋上箭頭）與 `restore`（時鐘＋逆時針箭頭）（D17）。
- `web/src/components/PermissionDialog.tsx`：i18n + `audience`（D12）。
- 頂欄（`ChatListRail` / 麵包屑元件，P8 時定位）（D14）。
- `web/src/lib/i18n.tsx`：新增／改寫的字串全部 zh-TW + en 各一份（含 D16 的五個 code）。
- `src/workspace_app/agent/tools.py`：D15 分段；`install_skill_impl` 改讀 struct 成句。
- `src/workspace_app/apps/skills.py`：`skill_folder_in_the_way` 回事實 struct（D16）。
- `src/workspace_app/api/skill_hub_routes.py`：五句改 `{code, …}`（D16）；`web/src/api/skillHub.ts` 的 `refused()` 讀 code 翻譯、無 code 才落到句子。

## 階段

每一階段：先寫會紅的測試 → 綠 → 一個守衛一個突變探針（各紅在自己的測試）→ **版面類的改動在真 Chromium 量 1280 與 390 兩個寬度**（換行用 `getClientRects().length`，不用高度）→ commit。流程照 CLAUDE.md：review 完才開 CI。

| | 做什麼 | 驗收 |
|---|---|---|
| **P1** | D1 `ModalShell` 預設 padding；#822 的 commit 併入（cherry-pick，#822 關閉） | 5 個沒傳 `panelStyle` 的呼叫者截圖前後對照（轉移、開新 item、ShareChat、EntityRecord、ManageChats）；3 個 `padding: 0` 的不變；面板列在 1280／390 的量測數字同 #822 |
| **P2** | D2 搜尋不失焦；D7 拿掉 badge；D9 fork 來源行 | 測試：打字→查詢鍵改變→輸入框是同一個 DOM node 且 `document.activeElement` 仍是它（happy-dom 可驗）；badge 不再渲染；「我的」視角 fork 卡片含「fork 自 …」 |
| **P3** | D3 silentError（picker + 條目頁六個 mutation）；D10 `PageNotice` + 轉移／刪除後的成功提示 | 測試：被拒的 install 只出現一處錯誤文字、`reportWriteFailure` 未被呼叫（spy）；轉移成功 → 列表頁 `role="status"` 含新 owner 顯示名；刪除同 |
| **P4** | D6 picker 三段文案；D8 已安裝標示；D13 picker 窄寬 cluster | 測試：按鈕文字、intro 兩段、缺 tool 句形；同名條目的鈕 disabled 且列上有「已有同名 skill」；parity：picker 標示的集合 == install 路由會拒絕的集合（同一個 item 資料）；Chromium 390 量測：安裝鈕在文字下方、無溢出 |
| **P5** | D4 「有新版」badge + 依來源分句；D5 `undo` glyph；D13 面板底部提示隱藏 | 測試：`update_available` 的列有 badge 文字；hub 副本與 package 副本的 tooltip／提示各自的句子；Chromium 390：底部提示不渲染、按鈕一行 |
| **P6** | D11 開新 item 帶 `?skill=` 提示 | 測試：條目頁「開新 item」連結帶 `skill=`；AppNewItem 讀到就畫提示句、沒有就不畫 |
| **P7** | D12 `PermissionDialog` i18n + `audience` | 測試：hub 呼叫顯示「平台上所有人」、item 呼叫顯示既有句；en 版兩者；未傳 `audience` 的既有呼叫者字不變（parity：對每個呼叫者 render 一次比對舊字） |
| **P8** | D15 工具回話分段 | 測試：`publish_skill` 的回話以空行分段、清單後的句子不在清單裡（用前端同款 markdown 解析驗，不是字串比對） |
| **P9** | D16 五句改 code + 前端翻譯 | 後端：每句一測（狀態碼 + code + 參數）；`skill_folder_in_the_way` 的 struct 由 tool 與路由各自成句（parity：tool 的英文句和改前逐字相同）；前端：五個 code 各自的 zh-TW／en 句、未知 code 的後備句；demo 5 的拒絕畫面重錄 |
| **P10** | D14 頂欄窄寬 | Chromium 390：「切換」單行、麵包屑最後一段完整可見；1280 不變 |
| **P11** | D17 面板列四顆圖示各自專用的 glyph + 四顆都有 tooltip | 測試：Publish 用 `publish`、Reset 用 `restore`、Refresh 用 `refresh`、Download 用 `download`，四個 glyph 互不相同、且和底部「匯入」的不同；四顆都有 `title`（= aria-label 的動詞句）；Chromium 3× 截圖看得出四顆各是什麼 |
| **P12** | 三把鏡頭自審 → 一輪 review（四把鏡頭並行）→ 修 → CI → /web-demo 補錄受影響的段（picker、面板、轉移、被拒） | 錄影裡看得到：留白、「安裝」、「有新版」、成功提示、中文的拒絕句 |

## 知情的風險

- **D1 是整站的視覺改動**：預設 20 會讓那 5 個沒傳的對話框多出留白（這正是目的），但也要確認沒有呼叫者是「刻意沒傳、靠子元素自己留白」——P1 逐個截圖看。
- **D12 動共用元件**：`PermissionDialog` 的其他呼叫者（item 分享）文案不變由 parity 測試釘住。
- **D14 動共用頂欄**：範圍最大、和 hub 無關；若 review 認為該另開，P10 可以獨立拆出去不影響前面。
- **D16 改了路由的 `detail` 形狀**：唯一客戶端是這個前端，且前端保留「無 code 就顯示句子」的後備，所以舊前端撞到新後端（滾動更新中）只是回到今天的英文句，不會壞。

## migrations 帳

無設定、無資料、無新 JobType；D16 改的錯誤形狀只有自家前端讀——**不需要** `docs/migrations.md` 條目。`docs/design-history.md` 加一列指到本計畫。
