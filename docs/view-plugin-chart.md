# chart：互動圖表 view plugin（#847 #848）

`view: chart` 讓一個 `*.ai.yaml` 檔案畫成**活的圖表**：可以框選、套索、點圖例、看 tooltip，
AI 主張的那群資料一打開就被點亮。它是平台的第一個 **runtime view plugin**，也是寫 plugin 的範例：
一個資料夾，裡面有前端、沙盒端、skill 與調校情境四個部分，不用改 SPA 原始碼也不用重 build。

## 誰寫、怎麼出現

- **AI 寫**：主張資料裡的關係、趨勢、分布或比較時，AI 會用 `write_file` 寫一份
  `views/<它要表達的>.ai.yaml`，再呼叫 `show_file`。對話裡出現一張卡片，點開就是圖表。
  `show_file` 會先跑 plugin 的 `validate`：檔案不合規格、欄位不存在，或 highlight 一列都沒中 / 全部都中，
  都會被拒絕，卡片不會出現，AI 會拿到錯在哪裡；通過時回覆會多一行摘要，例如
  `highlight matches 3/25 rows; fail_rate 0.02–0.41`。
- **人寫**：同一個檔案格式，手寫也一樣會渲染。

## 規格在哪裡

規格只寫在兩個地方，兩邊由測試綁在一起：

- **給人和 AI 讀的參考**：`view-plugins/chart/skill/SKILL.md`。這份就是 AI 用的 skill，
  範例由測試保證能通過 schema。
- **機器驗證用**：`view-plugins/chart/sandbox-src/src/chart_view/spec.schema.json`，只有這一份。
  沙盒端的 `validate` 和瀏覽器端的 renderer 讀的都是它，`view-plugins/chart/spec-corpus/` 的每一個範例檔同時餵給兩邊，判定必須一致。

一句話摘要：Vega-Lite 子集寫成 YAML（`mark` / `encoding` / `transform` / `layer`），
再加上我們自己的 `source`（CSV、TSV、parquet 或 `{entity: <type>}`）、`keys`、`highlight`、`bin_threshold`。
不認得的鍵一律報錯。

## 連動：有名字的 marking（#856）

- 兩張以上的圖寫同一個 `marking: <名字>`，就連在一起：在其中一張框選、套索、點圖例或點圓餅圖的一片，其他圖裡對得上的列會亮起、
  其餘變暗。對不對得上看**同名欄位**：選取會投影到 `keys:` 列出的欄位，寫進那個 marking；每張圖用自己資料裡有的
  同名欄位比對。沒有 `keys:` 的圖可以被點亮（文字與數字欄位），但它的選取不會寫進 marking；它只把時間欄位
  畫成座標，不帶 marking 用的字串，所以時間欄位不會被比對（整張不變暗）——要在時間欄位上連動，就把它寫進 `keys:`。
  channel 上有 `aggregate` 的欄位（例如圓餅圖的 `theta: {field: item, type: quantitative, aggregate: count}`）裝的是聚合值
  （計數、平均），不是那個欄位原本的值，所以既不拿來比對、也不寫進 marking，即使它列在 `keys:` 裡；
  這張圖用它其他的同名欄位連動（上例是 `color` 的欄位）。
- 有 `keys:` 的圖打開時，如果它的 marking 還是空的，檔案裡的 `highlight:` 會當作起始選取寫進去（每次打開只寫一次）；
  已經有人選了就不覆蓋。marking 被清空後，接在上面的圖全部不變暗，不會各自退回自己的 `highlight:`。
- 機制本身不懂任何領域：marking 只是「欄位名 → 一組值」，都是字串。所以 marking 有兩個以上的欄位時，
  點亮的是**各欄位值的所有組合**：勾 (A, 1) 與 (B, 2)，(A, 2) 與 (B, 1) 也會亮，計數可能比勾選的多。
  為了讓這件事看得見，每個顯示計數的地方都寫出 marking 的欄位（以 `keys: [group, item]` 為例）：
  縮圖牆「12 of 48 marked · by group, item」、接了 marking 的圖表「16 selected · by group, item」、
  表格「filtered by … · 4 of 48 rows · by group, item」、標頭的「by group, item」，
  以及訊息上的 chip「by group (2), item (2)」。
- 框選與套索對圓餅圖與 rule 以外的每一種 mark 都有效（rule 是參考線，不對應任何列），都以畫出來的位置判定：line 與 area 看點（疊起來的 area 以它疊上去的高度算）、
  heatmap 與 grid 看格子中心、boxplot 看箱子（q1–q3；只碰到鬚不算）、errorbar 看中間那條線。
  圓餅圖用點的：點一片就選那一片的列，再點同一片或點空白處就清掉點選的那份（其他 view 寫的選取與圖例的選擇不動）；
  只有圓餅圖的圖，工具列只有 ✕。
- 另一張 view 寫了 marking 之後，選取有寫進這個 marking 的圖會丟掉自己的選取（「N selected」、框、點選的那一片、
  圖例隱藏的項目），畫面只照 marking 點亮；選取不寫 marking 的圖（沒有 `keys:` 的）保留自己的。
- 調整大小（包括窄到換成精簡版面）不會丟掉選取、框與圖例隱藏的項目；改 view 檔或資料重新整理才從頭來。
- 圖寬不到 320 px 時用精簡版面：顏色鍵（色條，或 grid 依類別上色時的類別色塊）放到圖的下方，但只在圖本身
  還保有至少一半高度時；放不下就不畫，圖上方的說明列寫「colour key hidden (too short)」，顏色照舊。
  依顏色分組的圖例（例如分組的 scatter、line、bar）仍在圖上方，不受影響。
- 在哪張圖選取，那張圖也照 marking 點亮（框留著，可以看、可以清），和其他 view 一致；只有不寫 marking 的圖
  （沒接 marking、或沒有 `keys:`）才把框外的點變灰。
- `stack: true` 在任何軸上都照值疊，不用先 `aggregate: sum`：同一個顏色在同一個位置有好幾列，就畫成**一段**，
  高度是這些列的總和；tooltip 寫「(sum of N rows)」，框選、套索或點到這一段就選到全部 N 列，其中有一列被 marking
  點亮，這一段就亮。某個顏色在某個位置沒有資料時，那裡算 0（收窄到沒有）；對數軸上，底下沒有正值的位置則留空。
  想看一列一列的值，就不要疊。
- 對數軸上 0 與負值沒有位置，那些點不畫，圖上方的說明列寫「N values at or below 0 not drawn on the log y axis」。
- 圖上的「N selected」只有在選取寫進**這張圖現在接的** marking 時才接「· by <欄位>」；沒有 `keys:` 的圖、斷開 marking
  時做的選取，或寫進的是另一個 marking，都只寫「N selected」。view 檔改名不算別的 view 寫的。
- view 標頭的 marking 選單（標籤圖示＋`<名字> ▾`）可以把這張圖改接到別的 marking、新開一個，或斷開。這是**你自己的畫面狀態**，
  存在瀏覽器裡、不會改寫檔案；要永久改就改 YAML 的 `marking:`。
- 連動的範圍是一個 item。同一個 item 開在好幾個分頁（例如聊天模式開出來的純編輯區頁面）也是同一組 marking。
- AI 可以用 `show_file(layout=…)` 把幾張連動的圖一次放進分割窗格；送訊息時，composer 上方的 marking chip
  會把選取寫成 `.markings/<名字>.json` 給 AI 讀（見升級手冊 [#856](migrations.md#pr-856)）。
- **表格也接 marking**：內建的 entity `table` 與 `csv-table` 寫 `marking:` 就加入連動。marking 裡有值時，表格只剩
  被點亮的列，表頭上方一條「filtered by <名字> · 3 of 25 rows · by <欄位> · show all」；按 show all 顯示全部列、被點亮的反白
  （這個選擇記在你自己的瀏覽器）。比對規則和圖表相同：每個共同欄位的值（用圖表寫 marking 的同一種文字）都在集合裡。
  表格和 marking 沒有共同欄位時，顯示全部列並說明「no column in common」。
- **在表格上勾選也會寫 marking**：寫進 `keys:` 的欄位，沒有 `keys:` 就用 marking 本身的欄位；兩者都沒有就不寫，
  標頭會說原因。勾選的那張表自己不被過濾（只反白），免得一勾其他列就消失；表格只對自己有的值做決定，不會取消
  別張圖才有的值。沒接 marking 的 entity 表格，勾選照舊是批次編輯。
- 圖表工具列的 ✕ 會清空 marking，包括打開時由 `highlight:` 寫進去的起始選取。
- **另存成表格**：marking 選單與訊息上的 chip 都有「Save as table」，把被點亮的列（來源的每一欄，套用該 view 的
  `transform:`）寫成 workspace 裡的 `markings/<名字>-<yyyymmdd-hhmm>.csv`，檔案樹看得到、`csv-table` 打得開、AI 讀得到。
  寫檔算進 workspace 額度，需要新增檔案的權限。chip 只存它當時送出的值：同名 marking 之後又送過一次，舊 chip 會拒絕並說明。
  這個動作由宣告了 `"provides": {"marking_rows": …}` 的 plugin 提供（chart 宣告了），平台不寫死 plugin 名字。

## 縮圖牆：`facet:`（#857）

`grid` 加上 `facet:`，就不是畫一張圖，而是**每個群組畫一張小圖**，排成一面可以捲動的縮圖牆，
幾百到幾千組都行。寫法見 `SKILL.md` 的 Facet 段落（範例由測試保證能通過 schema）；這裡只講它怎麼運作。

- **第一次打開時，沙盒建一份快取**（讀一次來源檔），之後捲動、換排序、放大都從快取取，不再讀來源。
  來源檔或 spec 中會影響內容的部分（facet 欄位、排序欄位、x / y / color 的欄位與型別、transform）一改，
  下次打開就重建；只換排序方向、標題或配色則不會重建。
- **只載入看得到的部分**：畫面外的縮圖不會向沙盒要資料，捲到附近才載入。
- **縮圖和大圖畫出來的像素完全一樣**：兩邊用同一份量化與配色（有測試逐像素比對）。點縮圖旁的 ⤢ 可以放大，
  滑鼠停在格子上會顯示該格的**精確值**（不是量化後的顏色值）。
- **選取是依排名，不是依畫面上的元素**：點一張、shift 點另一張，會選取兩者之間的所有排名；也可以直接輸入
  「第 a 到第 b 名」，或在牆上拖出一個框（從空白處拖＝取代選取，按 Shift 拖＝加入；框碰到的格子都算）。選取會把範圍內**每一組**（包括還沒捲到、還沒載入的）的 facet 欄位值寫進這張圖的 marking，
  同一個 marking 上的其他圖會亮起對應的列。facet 有好幾個欄位時，marking 是**每個欄位各記一組值**，
  所以選了 (L1, 1) 和 (L2, 2)，(L1, 2) 和 (L2, 1) 也會一起亮——這是 marking 本身的比對規則。
- **排序可以換成任何欄位**：牆上的選單列出每一欄。每組只有一個值的欄位直接用那個值排；每組有多個值的欄位，
  選一個統計來排（數字：mean / median / min / max / count；文字：distinct / count；日期：min / max / count），
  升冪降冪都可以，排的是全部群組。spec 裡對應的是 `facet.sort.stat`。換欄位或統計會重建快取，只換方向不會。
- **牆旁的疊圖面板**：把選取的群組（沒選就是全部）在每個格子上疊成一張，欄位與統計自選；按 Set as B 把目前的選取
  存成 B，面板同時畫 A、B 與 A − B（發散色階）。疊圖在沙盒裡、依快取的版面算。
- **第一次打開會顯示建置進度**：建置每做完一步印一行（讀檔、讀到幾列、分組、寫快取），畫面每秒更新一次，所以快的步驟可能一閃而過、只看得到其中幾行；快取還在的話只顯示「Opening the gallery…」。
- 顏色欄是類別時，每個類別一種顏色，牆與放大圖都有圖例列出類別名稱。
- `show_file` 的 `validate` 對 `facet:` 檔案會直接建一次快取（和打開時同一個函式），所以打開時不會再被拒絕，
  也會馬上打開；代價是 `show_file` 要付一次建置時間（在同一個沙盒指令時間上限內）。
- **疊圖與相減不需要 `facet`**：`transform:` 裡對 lattice 欄位做 `aggregate`，就是把所有群組疊成一張；
  `diff` 則是兩組相減（見 `SKILL.md` 的 Transforms）。
- 快取放在沙盒的 `.home/.cache/views/`，不算 workspace 額度、不備份、隨沙盒回收；上限與容量估算見升級手冊
  [#857](migrations.md#pr-857)。`facet:` 的來源必須是表格檔，`{entity: …}` 會被拒絕。

## 時間與時區

- 帶時區的時間欄位（例如 parquet 的 `timestamp[…, tz=Asia/Taipei]`）在軸、tooltip 與縮圖標籤上都用**它自己的時區**
  顯示，並寫出時區名稱（軸名 `at (Asia/Taipei)`）；沒有時區的欄位照原樣（UTC）顯示，不隨看的人的瀏覽器時區改變。
  時間顯示到欄位資料實際有的最細單位（有時分就到時分，有秒就到秒），只有日期的欄位只顯示日期。
- 篩選（`equal` / `oneOf` / 大小比較 / `range`）、`diff` 的兩邊與 `highlight: values` 在日期欄位上用同一種讀法：
  datum 的日期寫法、marking 寫出的文字（到奈秒）、或 epoch 毫秒；沒寫時區的時間用欄位自己的時區讀，
  時鐘撥回而出現兩次的時間兩個都算，撥快而不存在的時間一個都不算，大小比較則會拒絕並說明。
  CSV 的日期欄位（每格都是日期文字）也照日期比較，和圖上畫的一致。
- 時間軸上沒寫時區的 `datum`（例如 rule）也放在軸欄位的時區；落在時鐘撥動時重複或不存在的時間會被 `validate` 拒絕，
  並列出帶 offset 的寫法。rule 的標籤照軸的讀法顯示（時間、類別或格子）。
- pandas query（`filter: "…"`、`where:`）裡和帶時區欄位比較的時間要寫上時區，沒寫的會被拒絕並說明。

## 對話卡片的縮圖

`show_file` 秀出的 chart、縮圖牆或 layout 卡片，捲進畫面時會用同一個渲染器畫一張小的靜態縮圖（layout 每格各畫各的），
點開才是活的圖表；用的是和打開時同一組沙盒呼叫，所以點開會沿用。畫不出來就是原本的純檔案卡片。
這是 view kind 的一個選用能力（`registerViewKind` 的 `Thumbnail`），沒提供的 kind 維持純檔案卡片。

## 計算在哪裡跑

聚合、篩選、差異、分箱都在**這個 item 自己的沙盒**裡跑（`exec` 同一套 uid / cgroup 隔離），
瀏覽器只收結果，數值與類別都用二進位 base64 傳。打開圖表時如果沙盒還沒醒，會先喚醒它。
renderer 呼叫沙盒時傳的是 view 檔的**路徑**（加上內容摘要當快取鍵），不是 spec 全文，所以超過 128 KB 的 spec 也畫得出來。

- 沙盒端是一個 tool bundle，用 isolated launcher 啟動：使用者自己 `pip install --user pandas`
  **碰不到**它用的 pandas / pyarrow。
- `source: {entity: <type>}` 跑的是平台自己的 `EntityStore.query`，透過 `view-plugins/sdk-python`
  打包進 bundle，所以圖表看到的紀錄和 table view 完全一致。
- `kind: http`（正式環境）：bundle 已經烤進 sandbox-host 映像的 `builtin/chart`。
  `kind: local`：開機時從 plugin 目錄複製到合併後的 tools root——前提是那個目錄裡有 `sandbox/`。
  API 映像只帶 web 半邊，所以要掛一個用 `view_plugin build` 裝好的目錄，做法見
  [升級手冊 #855](migrations.md#pr-855)。
- `kind: docker`：不支援 tools，所以也不支援 chart 的沙盒端。

## 運營方要知道的事

- **關掉它**：把 `chart` 從 plugin 目錄（`view_plugins.dir`）移除。
  在某個 item 的 skill 偏好把 `chart` 關掉，只會拿掉那份 skill（AI 不再讀得到規格說明）；
  `## Available views` 裡 chart 的那幾行仍在，因為它看的是 app 有沒有 `write_file` 與 `show_file`。
- **為自己的模型重調 skill**：
  1. 執行 `uv run python -m workspace_app.view_plugin tune chart`，它會用
     `<plugin 目錄>/chart/scenarios/` 的情境，外加一組不給 skill 的對照組來評分。
  2. 直接改 `<plugin 目錄>/chart/skill/SKILL.md`，重跑一次。

  這份 skill 只有一個檔案，改完**下一輪對話就生效**，不需要 Refresh 或重 build。
  `tune` 會替情境評分，但這些分數不是 CI 的關卡，只是讓你在自己的模型上比較改前改後。
- **prompt 成本**：凡是同時擁有 `write_file` 與 `show_file` 的 app，每一輪 prompt 都會多兩樣東西：
  - `## Available views` 整段，約 610 字元（實測 606）：標題、一句說明，加上 chart 的四行（圖表、grid、
    `facet:` 縮圖牆、疊圖與相減）與 csv-table 的一行（接 `marking:` 的表格）；
  - skill 索引裡 `chart` 那一行，約 230 字元。

  SKILL.md 本文只在 AI `read_skill('chart')` 時才載入，不是每輪的成本。
