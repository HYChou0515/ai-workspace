# 從外部系統把工作交棒進來（給外部系統的開發團隊）

你有一套既有的系統，使用者在上面做完一輪分析，想在這個平台上讓 AI 接力往下做。
這頁講你要打哪幾支 API、以及三個會讓你安靜踩到的地雷。

**平台這邊不需要為你新增任何端點。** 下面用到的全部是現成的路由，你只要照順序打。

## 先講清楚這件事的形狀

一個真實問題，在你那邊常常被拆成好幾筆分析；在這邊我們希望它們**收斂成同一個工作項目**
（item），這樣 AI 才有完整的上下文可以接力。

但**哪幾筆算同一題，只有人知道** —— 你那邊沒有欄位可以把它們串起來，所以平台不會替你猜。
正確的作法是：**讓使用者在你的畫面上挑**要接到哪個既有 item，或開一個新的。

| 規則 | 意思 |
|---|---|
| 多對一 | 多筆你那邊的分析，可以收斂到同一個 item |
| 同 item 不重複 | 同一筆分析已經進過某個 item，就不要再進一次 |
| 跨 item 不限制 | 同一筆分析要同時掛到兩個不同 item，是允許的 |

## 你要做的四件事

以下路徑都在 `/api` 底下。RCA app 的資料模型路徑是 `rca-investigation`；
其他 app 各有各的模型名稱，可以從 `/api/openapi.json` 查到。

### 1. 列出候選 item（給使用者挑）

```
GET /api/rca-investigation/data
      ?limit=100
      &sorts=[{"type":"meta","key":"updated_time","direction":"-"}]
```

回來的每一筆都帶著自己的 `external_refs` 欄位，長這樣：

```json
[
  {
    "title": "烤箱溫度飄移",
    "external_refs": ["legacy-rca:12345", "legacy-rca:12346"],
    "severity": "P1",
    "status": "triaging"
  }
]
```

所以「這筆分析是不是已經進過某個 item」**你在自己的前端就能判斷完**，
不用多打任何一支 API：把使用者這次要交棒的編號，拿去比對每筆的 `external_refs` 就好。
已經進過的那個 item，就在畫面上標示或停用。

排序請用 `updated_time`（最後被動過的排最前面），**不要用 `created_time`**。
原因見下面的地雷二。

### 2a. 使用者挑了既有 item → 上傳檔案，再記一筆

上傳（原始位元組直接放 body，不是 multipart）：

```
PUT /api/a/rca/items/{item_id}/files/legacy-rca-12346/readings.csv
Content-Type: application/octet-stream
<檔案內容>
→ 204 No Content
```

記錄這個 item 已經吸收了這筆分析：

```
PATCH /api/rca-investigation/{item_id}
Content-Type: application/json

[{"op": "add", "path": "/external_refs/-", "value": "legacy-rca:12346"}]
```

這是 RFC 6902 JSON Patch 的**追加**寫法。請務必用 `add` 到 `/-`，
**不要**把整份 `external_refs` 重送一次 —— 那會蓋掉別人同時記進去的東西。

### 2b. 使用者要開新的 item

```
POST /api/a/rca/items
Content-Type: application/json

{
  "title": "烤箱溫度飄移",
  "external_refs": ["legacy-rca:12345"],
  "permission": {"visibility": "public"},
  "severity": "P1"
}
→ {"resource_id": "rca-investigation:...", "seeded": ["/SOP.md", ...]}
```

拿到 `resource_id` 之後，再照 2a 的方式逐檔上傳。

### 3. 把使用者送過去

```
https://<平台網址>/a/rca/{item_id}
```

**請在檔案都上傳完之後才跳轉**，這樣使用者一到就看得到完整的東西，
不會看到一個半空的工作區。

## 三個地雷

### 地雷一：`limit` 不帶，就等於全撈

這邊的 `limit` 預設值是一個哨兵值（約 42.9 億），**不是頁大小**。
你忘了帶 `limit=100`，這支 API 會安靜地把整張表撈給你 —— 不會報錯，只會越來越慢。
**每一次都要明確帶 `limit`。**

### 地雷二：排序用 `created_time` 會讓收斂失效

清單有上限，所以掉出範圍的 item 使用者就看不到 —— 看不到就會再開一個新的，
於是同一題又散開了，正是這整套機制要防的事。

用 `updated_time` 排序可以幾乎消除這個問題：item 只要被交棒過（上傳檔案、追加編號），
時間戳就會更新、被推回最前面，所以**正在辦的案子永遠在第一頁**。
用 `created_time` 的話，一個三個月前開、但這禮拜天天在用的 item 會被埋在後面，
偏偏那正是最該被挑中的那一個。

### 地雷三：`permission` 不帶，item 生下來只有自己看得到

建立 item 時如果**沒有**明確帶 `permission`，它預設是私人的。
你不會收到任何錯誤，但你的使用者的同事在清單上**看不到這個 item**，
於是同事會自己再開一個 —— 同一題又裂成兩個，而且沒有任何徵兆。

所以建立時請明確帶：

```json
"permission": {"visibility": "public"}
```

## 一件請你不要做的事

**不要拿 `external_refs` 當查詢條件**（例如想問「這個編號被哪些 item 收過」）。
這個欄位刻意沒有建索引，對它下條件在正式環境會退化成**子字串比對** ——
`legacy-rca:1` 會match 到 `legacy-rca:12345`，而且不會報錯，只會給你錯的答案。

正確做法就是第 1 步：撈一頁回去，在你自己的前端比對。

## 平台這邊要配合的一件事

你的網頁要能打這邊的 API，我們必須先把你的網域加進白名單，否則瀏覽器會在請求送出前就擋掉。
請把你的來源網址（例如 `https://legacy-rca.corp`）給維運，設定在：

```yaml
server:
  cors_allowed_origins:
    - "https://legacy-rca.corp"
```

身分沿用共用登入，所以**你不需要、也不應該在參數裡帶使用者 id** ——
從瀏覽器打過來，這邊自己就知道是誰。

## 編號格式

`external_refs` 的每個值請用 `<系統代號>:<紀錄編號>`，例如 `legacy-rca:12345`。

平台把它當成不透明字串：只比對，永不解析。`<系統代號>` 的作用只是讓不同來源系統的編號
不會互撞，你自己取一個穩定的名字即可。
