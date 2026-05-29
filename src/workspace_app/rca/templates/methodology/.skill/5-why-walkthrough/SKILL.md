---
name: 5-why-walkthrough
description: 引導完成 /5-why.md 的 5 Whys 連環追問。當用戶問「為什麼」、要求 root cause、或 /5-why.md 還是空白模板時使用。
---

# 5 Whys 流程

5 Whys 是把單一可觀察症狀,**沿因果鏈往上問五層**,直到追到 root cause(可採取對策的最深層原因)。

## 進入條件

開始 5 Whys 之前,先確認:

1. `/brief.md` 已寫清楚 **what / where / when / impact** — 沒有 brief 就先寫 brief。
2. 已決定好**起點症狀**(例如「panel inspection 抓到 voids 比率上升到 3.2%」),不是模糊抱怨。
3. fishbone 上已標出**候選類別**(6M),5 Whys 是把其中一條鏈追下去 — 兩者互補。

## 如何寫 /5-why.md

每一層格式:

```
Why 1: <為什麼會發生 X?>
A: <答案,要可驗證,不是猜測>

Why 2: <為什麼 A 會發生?>
A: …
```

關鍵:

- **每層 A 都要有證據** — 數據、量測、訪談、設備 log。空想出的「因為操作員不小心」是停止 5 Whys 的信號。
- **不止一條鏈** — 同一層 A 可能拆兩條;允許 5 Whys 是樹,不是線。
- **第五層之前找到 actionable root cause 就停**;堅持五層湊數會虛假。
- **每層自問:對策能套在這層嗎?** 不能 → 繼續往上;能 → 這是 root cause 候選。

## 退出條件

- root cause 對應的 **6M 類別**寫進 `/fishbone.canvas`(交叉確認你的兩個分析方法收斂在同一處)
- root cause 對應的 **corrective action**(對策)+ **preventive action**(避免再發)分行寫進 `/5-why.md` 末尾
- 把這個 5 Whys 的結論摘要丟進 `/report.v{N}.md` 對應段落

## 常見反模式

- **「人為失誤」結論**:幾乎永遠是錯的 root cause。問下去:**為什麼系統允許這個失誤?**(培訓?Poka-yoke?SOP 漏洞?)
- **「不夠認真」結論**:沒有 actionable 對策 = 還沒到 root cause。
- **跳過一層**:從「設備故障」直接跳到「採購便宜貨」,中間缺「為什麼維護週期不夠?」
- **混入解法**:Why 應該問因果,不是「為什麼我們沒做 X」。後者是行動討論,不是 5 Whys。

## 輸出

更新 `/5-why.md`,然後告訴用戶:
- root cause 的一句話總結
- 你在哪一層判定停止的(why 1/2/3/4/5)+ 為什麼這層可動作
- 對策建議 + 預防措施
