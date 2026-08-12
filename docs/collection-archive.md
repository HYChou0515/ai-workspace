# 知識庫封存包(Collection Archive)

一個 collection archive 是**一次把整個知識庫灌進系統**的方式:文件、context card、以及兩者之間的連結,全在一個 zip 裡。

它就是匯出功能產生的那個 zip,所以下面這幾件事其實是**同一個操作**:

- 還原備份
- 把知識庫從一個部署搬到另一個
- 讓外部系統整理好的資料批次進來

沒有第二套格式,也不需要轉檔器 —— 你的系統照這個格式吐 zip 就好。

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

## 匯入

**所有後端路由都掛在 `/api` 底下**(根路徑是前端 SPA)。

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

- **匯入是非同步的。** 回應是 `status: "indexing"`;文件要索引跑完才搜得到,卡片則是立即生效。
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
