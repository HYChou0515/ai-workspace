# 知識庫封存包(Collection Archive)

一個 collection archive 把文件、context card、以及兩者之間的連結裝在一個 zip 裡。它就是匯出功能產生的那個 zip,所以「還原備份」和「把知識庫從一個部署搬到另一個」是同一個操作。

沒有第二套格式,也不需要轉檔器 —— 你的系統照這個格式吐 zip 就好。

## 兩條匯入路徑,選哪一條看誰在等

| | **還原**(同步) | **批次匯入**(非同步) |
|---|---|---|
| 端點 | `POST /kb/collections/import` | `POST /kb/collections/imports` |
| 回應時機 | 全部寫完才回 | **立刻**回 `202` |
| 適合 | 還原自己匯出的備份,人在螢幕前等幾秒 | 機器推送、沒人看著、大小由來源方決定 |
| 大檔 | **會逾時**(207MB 曾等十分鐘後 504) | 不受請求時間限制 |
| 進度 | 無 | 可輪詢:幾份、進了幾份、哪幾份失敗 |

**外部系統推資料一律用非同步那條。**

⚠️ **網頁上的「匯入」按鈕走的是同步那條**,所以大包從網頁上傳一樣會逾時 —— 非同步這條目前只有 API。幾十 MB 以上請直接打 `POST /kb/collections/imports`。

### 非同步匯入

```bash
BASE=http://127.0.0.1:8000/api

# 建成新的 collection
curl -X POST "$BASE/kb/collections/imports" -F "file=@archive.zip"
# → 202 {"collection_id":"collection:…","import_id":"import-run:…",
#        "status":"queued","members":1043,"written":0,"finished":false}

# 或併進既有的(mode 同義於同步那條)
curl -X POST "$BASE/kb/collections/$CID/imports?mode=overwrite" -F "file=@archive.zip"
```

**回應立刻就有 `collection_id` 和 `members`**(封存包裡有幾份文件)。collection 當下就出現在列表上,只是還空著、正在填。

### 查進度

```bash
curl "$BASE/kb/collections/imports/$IMPORT_ID"
# → {"members":1043,"written":1043,"errors":[],"finished":true, …}
```

| 欄位 | 意思 |
|---|---|
| `members` | 封存包裡有幾份文件 |
| `written` | 已經**處理完**幾份。`mode=skip` 時,因為同路徑已存在而跳過的那幾份也算在裡面——這個數字是「不再欠處理」,不是「新增了幾份」 |
| `errors` | 失敗的文件,一份一行,格式 `路徑: 原因`;**最多 100 行**(超過的部分不列,失敗總數請用 `members - written` 算)。整批被拒絕時(例如無權寫入目標 collection),這裡會是一行不帶路徑的原因 |
| `finished` | 這次匯入是否已收尾(卡片已還原、暫存包已釋放) |

**`finished: true` 不代表全部成功。** 一份檔案讀不出來不會拖累同批其他檔案,所以會出現「收尾了、但 `errors` 有東西」——半套匯入看得出來是半套,這正是設計目的。真的要確認全部進去,比對 `written == members`。

**權限是在寫入的當下再查一次的。** 誰發起匯入,worker 就用誰的身分寫;所以只有你本來就能 `add_content` 的 collection 才寫得進去,把 run 的 `collection_id` 改指到別人的 collection 不會生效——整批會被拒絕,`written` 停在 0,原因寫在 `errors`。查詢匯入進度的那支 API 也只有**發起人本人**(或管理員)看得到,別人一律 404。

### 實作上的取捨

- **卡片在最後才還原。** 文件分批寫完後才處理 manifest 的卡片,所以 `finished` 之前查代號可能還查不到。
- **暫存的封存包在收尾時釋放。** 一份 200MB 的 blob 不會在解開後繼續留著;它只在這次匯入還可能重試的期間存在。
- **背景 worker 不會把整包讀進記憶體。** 封存包是**串流落地成暫存檔**,`zipfile` 直接在檔案上隨機存取,所以一個批次的成本是「一塊 chunk 的記憶體 + 一份封存包的暫存磁碟」,而不是每個併行 job 各扛一整包。worker 的 `ephemeral-storage` 因此是有宣告的——沒宣告會在匯入中途被驅逐,對呼叫端看起來就是「跑一半莫名停住」。

⚠️ **還沒做到的那一半**:**上傳**時封存包仍會在 API 記憶體裡完整存在一次(specstar 的 `Binary` 只吃 bytes,沒有串流或檔案握把的入口 —— specstar#447)。同步那條也是一樣,所以不是退步,但**呼叫端能推多大**這個上限,目前仍由 API pod 的 RAM 決定。

## 格式

```
archive.zip
├── .kb-collection/manifest.json     ← collection 設定 + context cards
├── M4/description.md                ← 一份文件
├── M4/raw-01.png                    ← 又一份文件
└── M4/annotated-01.png              ← 再一份
```

**除了 manifest 之外,zip 裡的每一個檔案都是一份文件**,存放在它在 zip 裡的路徑上。manifest 放在保留的 dot-path,所以永遠不會和真實文件撞名。

`.kb-collection/manifest.json`:

```json
{
  "version": 1,
  "collection": { "name": "缺陷庫", "use_rag": true },
  "context_cards": [
    {
      "keys": ["M4"],
      "title": "M4 — 邊緣崩角",
      "body": "一到兩句的權威定義",
      "reference_paths": ["M4/description.md", "M4/annotated-01.png"]
    }
  ]
}
```

manifest 完全可以省略 —— 那樣 zip 就退化成一次資料夾批次上傳(新建一個以檔名命名的 collection,不還原任何設定或卡片)。

### 卡片用「路徑」指文件,不是 id

`reference_paths` 寫的是**路徑**。文件 id 內含它所屬的 collection,所以直接搬 id 會讓每一條連結在匯入到別的 collection 時當場失效。匯入時會**依目標 collection 重新鑄造** id。

### `reference_paths` 是三態的

| manifest 寫法 | 重新匯入時的行為 |
|---|---|
| **整個欄位省略** | **保留**卡片現有的連結 —— 沉默不是主張 |
| `["a.md", "b.png"]` | 取代成這一組 |
| `[]` | 清空 |

這一條很重要:匯出之後才由人策展上去的連結,不該因為你重匯一次 archive 修個錯字就被清掉。真正的匯出檔一定會寫這個欄位,所以「匯出→匯入」的還原忠實度不受影響;沉默只保護手寫或殘缺的 archive。

### 卡片的附件是你逐條列出來的,不是系統猜的

`reference_paths` 裡寫什麼,那張卡就掛什麼。**沒有命名慣例、不看 title、不看資料夾名。** 上面例子把檔案放在 `M4/` 底下純粹是好整理;改成 `foo/bar.md` 然後在 `reference_paths` 寫 `foo/bar.md`,結果一模一樣。

同理:**沒被列進去的檔案仍然是一份正常文件**,只是不掛在任何卡片上。上面的例子有三個檔,卡片只列了兩個,所以 `raw-01.png` 是文件但不是那張卡的附件。

⚠️ **路徑打錯不會報錯。** 匯入只是把路徑編碼成 id,不驗證那份文件是否存在,所以一個 typo 會讓那條連結**靜默失效**——卡片照樣建起來,只是它擔保的文件不存在,而檢索到那份文件時也就帶不出這張卡。資料量一大,這是最容易累積的錯誤。

**產生 zip 時,讓同一個變數同時決定「檔案放哪」和「`reference_paths` 寫什麼」**,兩邊就不可能對不上:

```python
img_path = f"{folder}/{role}-{i:02d}{ext}"
members[img_path] = data          # 檔案放這裡
if role == "annotated":
    linked.append(img_path)       # 同一個變數,不會寫錯
```

別讓兩邊各自拼字串——那是唯一會出錯的地方。

### 匯入不會觸發 AI 產生卡片

manifest 裡的卡片是**資料**,跟文件一樣照收。這和「**卡片生成**」(AI 讀你的文件、自己草擬卡片給你審)是兩件不同的事,匯入完全不會觸發它:

| | 匯入 | 卡片生成 |
|---|---|---|
| 卡片哪來的 | **你寫在 manifest 裡** | AI 讀文件產生的提案 |
| 要不要 AI | 不用 | 要 |
| 怎麼觸發 | 上傳 zip | 按 collection 裡的 **Auto-generate**,或在設定裡打開 `auto_digest` |
| 結果落在哪 | 直接進 **Glossary** | 先進 **Review** 等人核准 |

`Collection.auto_digest` 預設 `False`,而匯入**不會設定它**(只還原 name / description / icon / use_rag / use_wiki / wiki guidance),匯出也不帶這個開關。所以一個匯入進來的 collection 不會自己開始產生卡片。

## 同步匯入(還原備份用)

**所有後端路由都掛在 `/api` 底下**(根路徑是前端 SPA)。這一條**寫完才回應**,所以只適合你自己匯出的備份;大的封存包請走上面的非同步路徑。

```bash
BASE=http://127.0.0.1:8000/api

# 建成一個新的 collection
curl -X POST "$BASE/kb/collections/import" \
     -F "file=@archive.zip"
# → {"collection_id": "collection:…", "status": "indexing"}

# 或併進既有的
curl -X POST "$BASE/kb/collections/$CID/import?mode=overwrite" \
     -F "file=@archive.zip"
```

`mode` 只在併進既有 collection 時有意義,而且**同時管文件和卡片**:

| | `overwrite` | `skip` |
|---|---|---|
| 文件(依**路徑**判定撞名) | 換成新的 | 保留原有的 |
| 卡片(依**代號**判定撞名) | 更新原本那張 | 原本那張不動 |

**重複匯入同一包不會長出重複卡片** —— 同代號的卡會被更新。多張卡共用同一個代號是被支援的:N 張共用代號的卡會配到 N 張不同的既有卡,不會全部疊在第一張上。

## 查詢

### 查代號:確定性,不經過模型

```bash
curl -X POST "$BASE/kb/collections/$CID/context-cards/lookup" \
     -H 'content-type: application/json' \
     -d '{"terms": ["M4", "M7"]}'
```

exact key 比對,毫秒級,不呼叫 LLM。卡片**不進語意索引**,匯入後立即可查,不必等索引跑完。

### 丟一張圖問「這是哪一種」

先建一個綁定該 collection 的 chat,再把圖**夾在訊息裡**送出:

```bash
CHAT=$(curl -s -X POST "$BASE/kb/chats" -H 'content-type: application/json' \
       -d '{"title":"分類","collection_ids":["'$CID'"]}' | jq -r .resource_id)

curl -N -X POST "$BASE/kb/chats/$CHAT/messages" \
     -H 'content-type: application/json' \
     -d '{"content":"這張是哪一種?",
          "image":{"data":"'"$(base64 -w0 shot.png)"'","mime":"image/png"}}'
```

回傳是 SSE 串流(`-N` 不能省)。這張圖**不會被存成文件** —— 平台用 VLM 描述它、拿那段描述去搜、然後丟掉。

搜到的文件會**把連著它的代號卡一起帶出來**,所以答案講得出「這叫什麼」,而不只是描述它看到什麼。這一點對圖片文件特別關鍵:圖片文件的內文是視覺模型寫的描述,**不可能出現人類指定的代號**,單靠文字比對永遠搆不到那張卡。

## 幾個會踩到的前提

- **「寫進去」和「搜得到」是兩件事。** 文件寫入完成之後還要跑索引才搜得到(`status: "indexing"` → `ready`);卡片不進語意索引,寫進去就能查。所以非同步匯入的 `finished: true` 只保證**寫完**,不保證**索引完**。
- **圖片需要 VLM。** 沒有配 `kb.vlm_llm`(或它連不上)時,圖片文件會停在 `error`,夾圖提問則直接回 400。
- **Office 檔裡內嵌的圖會被丟掉。** `.docx` / `.xlsx` 內嵌的圖片、文字方塊、圖形**完全不會被讀到,而且沒有任何提示**,文件狀態仍顯示完成。`.pptx` / `.pdf` 是整頁轉成圖交給 VLM,所以「這頁在講什麼」留得住,但**不會產生圖片向量**。要讓一張圖成為可被圖片檢索的獨立文件,就得讓它以**獨立檔案**的身分進 zip。
- **圖搜圖預設是關的**(`kb.image_embedder.kind: none`)。

## 走一遍

`scripts/check_collection_archive.py` 會把上面整條路實際跑過並斷言結果 —— 建 archive、匯入、等索引、查代號、確認卡片的連結指向真的文件、再匯一次確認卡片沒有變兩張:

```bash
# 只想看格式:產一個範例 zip 就停,不需要任何服務
uv run python scripts/check_collection_archive.py --keep /tmp/sample.zip --archive-only

# 對一台跑起來的服務走完整條路
uv run python scripts/check_collection_archive.py --base-url http://127.0.0.1:8000
uv run python scripts/check_collection_archive.py --ask     # 再加上夾圖提問(需要 VLM)
```

任何一步不如預期就以非零離開,所以它也可以當煙霧測試用。

**想理解格式,解開一個真的 zip 比讀上面的說明快。** 這也是 `--archive-only` 存在的原因:看格式不該先要求你有一台跑起來的服務。
