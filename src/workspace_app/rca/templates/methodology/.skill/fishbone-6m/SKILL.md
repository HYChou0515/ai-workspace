---
name: fishbone-6m
description: 用 6M(Man / Machine / Material / Method / Measurement / Environment)在 /fishbone.canvas 上窮舉候選因。當分析剛開始、用戶要求「列出所有可能原因」、或 5 Whys 卡住要回頭找漏掉的支線時使用。
---

# 6M Fishbone 分類

Fishbone(石川圖 / Ishikawa diagram)是把問題的所有候選因**分類窮舉**,避免漏掉支線。
6M 是製造業最常用的六大類別。把 `/fishbone.canvas` 的六個主骨各填一條以上。

## 六大類別 + 提問模板

### 1. Man(人)

- 操作者是否有經驗?哪一班次?是否新人 / 換班?
- SOP 是否清楚、最近有改版嗎?
- 培訓紀錄?上次 refresher 何時?
- 是否依賴個人經驗(隱性知識)?

### 2. Machine(設備)

- 哪台機台 / 治具?機台 ID + 序號。
- 上次 PM(預防保養)何時?是否到期?
- 故障 log 有異常嗎?
- 校正記錄、警報 log?
- 是否最近換零件、改參數?

### 3. Material(原料)

- 哪批原料?supplier、lot 編號、進貨日期。
- 進料檢驗報告有異常嗎?
- 原料變更歷史(換 supplier、換規格)?
- 儲存條件(濕度、溫度)是否符合?

### 4. Method(方法)

- SOP 版本?最近改動?
- 製程參數設定 vs. 實際 log?
- 順序、節拍、流程是否變更?
- 工序卡上的步驟、容差?

### 5. Measurement(量測)

- 量測工具的校正狀態?
- AOI / SPC / 抽樣計畫是否合理?
- 量測者間的一致性(Gage R&R)?
- 量測程式 / 樣板版本?

### 6. Environment(環境)

- 廠房環境(溫度、濕度、靜電、ESD)?
- 振動、電壓波動、氣壓?
- 鄰近製程的污染源?
- 時間相關性(早晚、季節)?

## 怎麼用 /fishbone.canvas

canvas 是 JSON 格式,schema 在 base prompt 已說明。要點:

- **每根主骨至少一條子骨**(寫不出來就標 N/A,而不是省略 — 缺骨就是隱性盲點)。
- **子骨上可以再分**(例如 Man → 班次 → 新人比例)。
- **可量化的證據要附上**(機台 ID、批號、抽樣比率) — 不是「設備老」這種模糊敘述。
- **顏色 / 標籤標出可信度**:已驗證 / 高度懷疑 / 待驗證。

## 跟 5 Whys 怎麼搭

- Fishbone **廣** — 一次掃完所有可能;5 Whys **深** — 沿一條鏈追到底。
- 先 fishbone 列候選,從**高度懷疑**那條開始 5 Whys。
- 5 Whys 結論回填到 fishbone 對應子骨上(標「驗證 root cause」);其他子骨保留 — 可能還有 minor causes。
- 多個 root cause 是正常的,fishbone 是視覺紀錄、不要清掉。

## 反模式

- 只填三、四骨就交差 → 漏掉 environment / measurement 是常見地雷
- 一上來就跳結論,filling backwards from a guessed cause
- 子骨寫「人為失誤」就停 → 推回 Man 的提問清單,問 SOP / 培訓
- 把對策寫進 fishbone(那是 8D / 報告的事)

## 輸出

更新 `/fishbone.canvas`,然後告訴用戶:
- 六骨填寫狀況(完整 vs. 哪幾骨 N/A 的理由)
- 最高度懷疑的 2–3 條子骨 + 為什麼
- 建議優先驗證的證據 / 量測
