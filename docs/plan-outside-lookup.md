# 斷網部署:AI 停下來請使用者去外面查,再把結果帶回來

**狀態:** P1–P8 完成(#897):P8 真瀏覽器量過 1280／390(卡片與頁面都沒有水平溢出、按鈕都在卡內),demo 已錄(MP4);
as-built 與計劃不同之處見 §8。
**來源標記:** 〔user〕= user 的原話或明確選擇;〔查證〕= 讀 `origin/master` 程式碼或文件確認的事實;
〔施工〕= 我定的實作細節,可推翻;〔指南〕= 出自公開的 UI/UX 指南,附出處。

## 1. 問題

後端部署在斷網環境〔user〕:AI 查不到網路,也問不到別的 AI。使用者的瀏覽器可以上網。現在 AI 遇到需要外部資料
的問題,只能說「我無法上網」或自己猜。

已經有的零件〔查證〕:

- `ask_user`(`agent/tools.py:ask_user_impl`):AI 出選擇題,turn 停下,答案是使用者的下一則訊息
  (`Message.answers` 記下答的是哪一個 tool call)。卡片的內容來自這次呼叫的 `tool_args`,turn 已經會存下來並串流。
- `request_env`(`agent/env_request.py`,#892):第二個「畫卡片、停 turn」的工具;**只要這個 turn 有 `ask_user`,
  它就跟著有**(`env_request.py` 的授權判斷),不必改每個 app 的 `app.json`。
- 停 turn 的機制只有一個:`api/litellm_runner.py:ask_user_stop_behaviour`(SDK 的 `StopAtTools`;`request_env`
  只在真的畫出卡片時停,被拒絕時不停,讓模型讀到原因)。
- `ask_user` 列在五個 `app.json`(`_template`、`playground`、`pm`、`rca`、`topic-hub`;`_template` 不是使用者看得到的
  App);KB 聊天沒有。workflow 的 agent step 有寫 `tools:` 就只拿它列的,**沒寫就拿 App 的全部**(含 `ask_user`)——
  所以「跟著 `ask_user`」不足以擋掉 workflow,見 §8 A8。

## 2. 決定

| # | 決定 | 來源 |
|---|---|---|
| D1 | AI 自己決定要出選擇題(`ask_user`)還是請使用者去外面查 | 〔user〕「他自己要決定是問選題還是讓user查google」 |
| D2 | 「查詢」模式:卡片上每個目的地一顆按鈕,按下去開新分頁直接搜尋;另外固定有「複製問題」 | 〔user〕「一個按鈕按下去就開啟新tab然後問google直接查」;複製鈕〔施工〕 |
| D3 | 目的地清單由部署設定 `server.lookup_targets` 決定;沒設就只有 Google;有設就整份取代 | 〔user〕「預設只有google」;鍵名與「整份取代」〔施工〕 |
| D4 | 帶回來:大的貼上區(貼網頁時把 HTML 轉成 Markdown,保留連結、表格)、可附檔(PDF、截圖)、選填來源網址;送出時存成 workspace 裡的檔案 | 〔user〕同意 |
| D5 | 「開網址」模式:AI 給網址,卡片上一顆「開啟這個網址」;完整網址明文顯示;只接受 `http://`、`https://` | 〔user〕「如果html裡面有超連結 ai要問user幫她開」;限制〔施工〕,user 同意 |
| D6 | 所有 app 的聊天有;KB 聊天沒有;workflow / 排程沒有 | 〔user〕「Kb不用特別處理」 |
| D7 | 有「查不到／不查了」按鈕,可選填理由;AI 下一輪改用手上的資料,並說明沒有外部佐證 | 〔user〕同意 |
| D8 | 查詢在卡片上可以先改,按目的地按鈕時送出去的是改過的內容;不另外寫 AI 規則、不加公司提醒文字 | 〔user〕「1就好」 |
| D9 | 一張卡只查一件事 | 〔user〕同意 |
| D10 | 在卡片上貼上圖片(截圖、「複製圖片」)就變成附件;不能附檔的人貼圖時卡片說明不能附檔,不默默吞掉 | 〔user〕同意(2026-10-09) |
| D11 | 貼上的網頁「處理越少越好」:外部圖片照轉出來的 `![說明](網址)` 留著、不提示;內嵌 `data:` 圖片不抽出;相對連結不補;只把貼上區說明改成「選取要的段落再複製(避免全選)」 | 〔user〕「處理越少越好才對」 |

## 3. 機制

### 3.1 工具 `ask_outside`〔施工:名稱與參數〕

```
ask_outside(why: str, query: str | None = None, url: str | None = None)
```

- `query` 和 `url` **恰好給一個**:給 `query` 是查詢模式(D2),給 `url` 是開網址模式(D5)。兩個都給或都沒給 → 回
  錯誤字串,turn 不停(照 `request_env` 的做法:被拒絕時不停,模型讀得到原因再改)。
- `url` 不是 `http://` / `https://` → 錯誤,不停。
- `why`:一句話說為什麼需要外面的資料,顯示在卡片上方。
- 成功時回給模型的字是平鋪直敘的重述(「Asked the user to look this up outside: …」),turn 停在這裡。卡片內容來自
  `tool_args`,和 `ask_user` 一樣,不另存一份。
- docstring(模型看到的說明)只寫能力與時機:這個部署是斷網的;需要公開的外部資訊時用它;要選擇時用 `ask_user`;
  使用者可能回「查不到」。不寫外洩規則(D8)。

### 3.2 停 turn 與授權

- `ask_user_stop_behaviour` 多一個工具:`ask_outside` 畫出卡片時停(成功回覆才停,錯誤不停),和 `request_env` 同一套。
- 授權:**聊天的 turn、而且有 `ask_user`**,才有 `ask_outside`(§8 A8)。結果:四個 App 的聊天有;KB 聊天沒有
  `ask_user` 所以沒有;workflow step 不是聊天,所以沒有(D6)。不改任何 `app.json`。

### 3.3 目的地設定(D3)

```yaml
server:
  lookup_targets:
    - name: Google
      url: "https://www.google.com/search?q={q}"
```

- 預設值就是上面這一筆。設了就整份取代,要保留 Google 就自己列進去。
- 每筆的 `url` 必須是 `http(s)://` 且含 `{q}`,否則啟動時拒絕(說出是哪一筆)——設錯在部署當下就知道,不是使用者按了才發現。
- 前端用一支唯讀路由拿清單(`GET /lookup-targets`〔施工〕),只要登入就能讀;`{q}` 由前端用
  `encodeURIComponent` 代入。

### 3.4 卡片(前端)

〔指南〕依據:

- **開新分頁**:NN/g〈Opening Links in New Browser Windows and Tabs〉——預設同一分頁,但原頁是使用者的「出發點」、
  要從多處蒐集資料時,開新分頁是合理的;此時要在點之前讓人知道(文字與圖示)。所以每顆目的地按鈕都帶 ↗ 圖示和
  「在新分頁開啟」的說明文字。<https://www.nngroup.com/articles/new-browser-windows-and-tabs/>
- **按鈕輕重**:Material 3 按鈕的強調層級——filled 留給完成流程的最終動作、outlined 給重要但不是主要的動作、
  text 給最低優先。所以「送出」是唯一的 filled;目的地按鈕與「開啟這個網址」是 outlined;「複製問題」「查不到／不查了」
  是 text。<https://material-web.dev/components/button/>

卡片內容,由上到下:

1. 標題「請幫我查」,下面是 `why`。
2. **查詢模式**:查詢放在**可編輯**的文字框(D8);一排目的地按鈕(outlined,↗);「複製問題」(text,按下後顯示「已複製」)。
   按目的地按鈕時,代入的是文字框**當下**的內容。
   **開網址模式**:完整網址以等寬字明文顯示(可選取);一顆「開啟這個網址 ↗」(outlined)。
3. **帶回來**(D4):
   - 大的貼上區。貼上時若剪貼簿有 `text/html`,先用既有的 `dompurify` 清掉 script / 事件屬性,再轉成 Markdown
     (新增 `turndown` + GFM 表格外掛〔施工〕);沒有 HTML 就照純文字貼。
   - 附檔(PDF、圖片;拖放或選檔)。
   - 選填「來源網址」。
4. 「送出」(filled)與「查不到／不查了」(text,展開一個選填理由欄)。
5. 送出後:送出鈕消失,欄位改唯讀,卡片留著當紀錄——照 `ask_user` 卡片現在的做法(`readOnly` 不用 `disabled`,
   已送出的字才讀得到)。重新整理後,有對應 `Message.answers` 的卡片顯示為已回答。

### 3.5 送出:存檔與回覆(D4、D7)

一支伺服器路由一次做完〔施工〕:`POST /a/{slug}/items/{id}/chats/{chat_id}/outside-answers`(multipart:
`tool_call_id`、`kind=found|not_found`、`content`、`source_url`、`reason`、附檔)。

- **權限與送訊息相同**(`converse`)。不讓前端自己寫檔,是因為有些聊天參與者只能聊天、不能改檔案;拆成兩個請求
  也會出現「檔案存了、訊息沒送」的半套狀態。
- `found`:寫 `lookups/<YYYY-MM-DD-HHMM>-<摘要>.md`(檔頭記 AI 的查詢或網址、使用者按的目的地、來源網址、時間;
  內文是貼上的 Markdown),附檔放進同名資料夾;然後用和一般送訊息相同的路徑送一則使用者訊息,`answers` 填這個
  `tool_call_id`。訊息內容:一行說明 + 檔案路徑 + 貼上的內容(超過上限只放開頭,註明完整內容見檔案〔施工:上限值〕)。
  寫檔走 `WorkspaceFiles`,所以容量上限照常擋(滿了回 507,訊息不送)。
- `not_found`:不寫檔,送一則固定格式的訊息「使用者沒有查到:<理由>」,`answers` 同上。
- 存檔名的「摘要」取自查詢或網址,去掉路徑不允許的字元;同一分鐘撞名就加序號。

## 4. 施工時我定的事〔施工〕

- 工具名 `ask_outside`、卡片標題「請幫我查」。
- 停 turn 不另做等待機制,照 `ask_user`(非阻塞;使用者的回覆就是下一則訊息)。
- 存檔目錄 `lookups/`;回覆路由由伺服器一次寫檔加送訊息。
- HTML→Markdown 用 `turndown`(+ GFM 表格),先經 `dompurify`。
- 目的地清單的路由與「整份取代」語意。

## 5. Phases

| Phase | 內容 | 完成的判準 |
|---|---|---|
| P1 | 這份計劃 | 文件測試、mkdocs `--strict` 綠 |
| P2 | `server.lookup_targets`(預設 Google、啟動時驗證)與 `GET /lookup-targets` | 設錯啟動失敗並說出哪一筆;路由回清單 |
| P3 | 工具 `ask_outside`、停 turn、跟著 `ask_user` 授權 | 成功停、錯誤不停;有 `ask_user` 的 turn 才有;workflow step 沒有 |
| P4 | 回覆路由:存檔 + 附檔 + 送 `answers` 訊息;`not_found` | 權限同送訊息;容量滿 507 且不送訊息;檔名撞名加序號 |
| P5 | 卡片:兩種模式、可編輯查詢、目的地按鈕、複製、開網址的明文網址 | 按鈕帶入的是改過的查詢;非 http(s) 網址不出按鈕 |
| P6 | 卡片:貼上轉 Markdown、附檔、來源網址、送出／查不到、送出後狀態 | HTML 表格與連結轉成 Markdown;script 被清掉;送出後鈕消失、欄位唯讀 |
| P7 | 文件:`docs/migrations.md`(所有 app 多一個工具、聊天出現新卡片——行為改變沒有開關)、`docs/configuration.md` | mkdocs `--strict` 綠 |
| P8 | 真瀏覽器量 1280／390 版面、對照引用的指南截圖、錄 demo(MP4) | 卡片在兩種寬度不溢出;按鈕層級與指南一致 |

## 6. 不做

- KB 聊天、workflow／排程(D6)。
- 外洩防護的第二、三層(AI 規則、公司提醒文字)(D8)。
- 一張卡多件事(D9)。
- 伺服器去抓網址:後端斷網,本來就抓不到。
- 自動判斷貼上的內容是否真的回答了問題:由 AI 下一輪自己讀。

## 7. prod 怎麼驗證

1. 在任一 app 的聊天裡問一個需要外部資料的問題(例如某個公開函式庫最新版本的變更),AI 出「請幫我查」卡片,turn 停住。
2. 改一下查詢,按「Google ↗」:新分頁打開,搜尋的是改過的字。
3. 在搜尋結果頁選一段內容(含連結或表格)複製,貼回卡片:貼上區是 Markdown,連結與表格還在。
4. 送出:`lookups/` 底下多一個檔案,AI 下一輪引用它作答。
5. 再問一次,這次按「查不到／不查了」:AI 說明沒有外部佐證,改用手上資料。
6. 部署有設 `server.lookup_targets` 時:卡片上的按鈕就是清單上的那些。

## 8. 施工後與計劃不同的地方(as-built,#897)

- **A1 卡片的來源**:不從 `tool_args` 畫,改成回覆尾端的宣告(`\n[outside-lookup]{json}`),和 `request_env` 同一套。
  停 turn 的判斷和聊天讀的是後端驗過的同一份(`declared_lookup`;前端的讀法用共用案例表
  `tests/fixtures/outside_lookup_cases.json` 釘在後端上,`web/tests/outsideLookupParity.test.ts`);匯出只拿掉同一個
  marker 後面的宣告(`shown_files.without_card_declaration`),不做同樣的驗證。
- **A2 網址的判斷**:前後端用同一個樣式,不各用自己語言的網址解析器——`http://a b` 在 Python 有 host、在 JS 會丟錯,
  後端會為一張前端畫不出來的卡停住 turn。樣式裡也**不用 `\s`**,把空白字元逐一列出(`_SPACE`):兩種語言的 `\s`
  不一樣(U+FEFF 只在 JS 算空白,U+0085 只在 Python 算),review round 1 抓到。
- **A3 權限**:送出要 `converse`;**存檔要 `add_content`**,和 #847 marking 同一條規則(review 抓過「只能聊天的人
  透過 marking 寫檔」)。只能聊天的人照樣能回,內容只在訊息裡、不存檔;卡片上**不出附檔區**,改說明文字不會存
  (`ChatItem.canAddFiles`,從 `useItemAccess().canAddContent` 傳下來)。
- **A4 檔名**:`lookups/<使用者當地日期 YYYY-MM-DD>-<摘要>.md`,日期由瀏覽器送(格式不對才用伺服器的 UTC 日期)——
  伺服器是 UTC,台灣早上 8 點前查的會被歸到前一天。不放時分;同名加 `-2`、`-3`。檔頭照記完整時間(UTC)。
- **A5 廣播帶 `answers`**:`user_message` 事件多一個 `answers`,另一個分頁的同一張卡當下收起,不必等重新整理後
  才發現送出被拒(409)。`ask_user`、`request_env` 的卡一起受益。
- **A6 錯誤文字**:容量滿用聊天送出的同一組文字(`CHAT_QUOTA_KEY`),其他用伺服器給的原因。
- **A7 附件 UI**:照 GOV.UK Design System〈File upload〉——看得到的標籤、次要樣式的「選擇檔案」、一直看得到的拖放區、
  「尚未選擇檔案」、選了列檔名且可移除。<https://design-system.service.gov.uk/components/file-upload/>
- **A8 只在聊天裡(D6)**:「有 `ask_user` 就有」不夠——workflow 步驟沒寫 `tools:` 時拿到 App 的全部工具,`ask_user`
  也在內,連排程跑的都會出卡片。改成**只有聊天的 turn**(`AgentToolContext.in_chat`,只有 `build_chat_turn` 設)
  才給。review round 1 抓到。
- **A9 送出的流程**:同一張卡在同一顆 pod 上一次只處理一個回覆(依 item + call id 上鎖,鎖有計數——放開時還有人在等
  就不能丟掉,review round 2 抓到);第二個在寫檔前就看到已回覆、回 409。寫完檔後再重讀一次對話,抓別顆 pod 先記下的
  回覆:那時收回自己的檔案、回 409。送出失敗時,**只在對話裡沒有「這一則」回覆(同一張卡、同樣內容)時**收回檔案;
  請求在送出受 shield 保護之後被取消則**不收回**——訊息可能在取消之後才存進去。已知的縫:取消落在 shield 之前(送出還在
  檢查能不能跑這一輪時),檔案會留著、沒有訊息提到;卡片仍開著,再回一次會存成 `-2`。兩顆 pod 的送出剛好重疊時
  仍可能都成功,不另做跨 pod 鎖。
- **A10 貼上的內容用檔案 part 送**:Starlette 的表單欄位上限 1 MiB,一頁中文(一字 3 bytes)就會超過;改成檔案 part,
  上限同單一檔案上限。
- **A11 用字**:
  - 訊息和檔頭記的是**實際搜尋的字**(使用者改過的查詢,D8),檔頭另記 AI 原本的查詢。
  - 「查不到／不查了」送「沒有查到／不查了:<查詢>」+「原因:<理由>」(計劃寫「使用者沒有查到:<理由>」,但同一顆
    按鈕也代表「不查了」)。
  - 工具給模型的回覆是「The user now sees a card asking them to look up … Wait for them …」(計劃寫「Asked the user
    to look this up outside: …」)。
  - 工具說明不說「這個部署斷網」,改說「你跑的伺服器可能上不了網」——工具不分部署都會給。
- **A12 卡片的寫入用 `useMutation`**(repo 慣例),送出中/錯誤狀態從 mutation 來。
- **A13 目的地名稱比對去掉前後空白**:`Google` 和 `Google ` 在卡片上是同一顆按鈕。
- **A14 health replay 照聊天回放**:回放 item 的對話(`source: rca`)時給 `in_chat`,聊天那一輪的工具清單才對得上
  (review round 2)。已知不準:replay 載入的若是 workflow run 的對話,回放會多一個 `ask_outside`(那一輪本來沒有)——
  replay 本來就不套步驟的 `tools` 子集,這是同一類既有的診斷誤差,不影響真正跑的那一輪(review round 3)。
