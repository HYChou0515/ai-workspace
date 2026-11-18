# Skill Hub 搜尋:分組比對、依使用次數排

**狀態:** P1–P4 完成(#1031);P5(demo、review、CI)進行中。
**來源標記:** 〔user〕= user 的原話或明確選擇;〔查證〕= 讀 `origin/master`(`eb11f47f`)的程式碼確認的事實;
〔施工〕= 我定的實作細節,可推翻。

## 1. 為什麼

AI 用 `search_skill_hub` 找 skill 時,查詢寫得越完整越找不到〔user〕「當ai把query下的越完整 越不可能找到」。
原因是比對規則只有一條:整句查詢(去頭尾空白、不分大小寫)必須**原封不動**是名稱或描述的一段
(`apps/skill_hub.py:matches_query`)〔查證〕——查「晶圓 良率 分析」,描述寫「分析晶圓的良率」就找不到。
符合的結果也不依相關程度排,只照 `visible` 的名稱、擁有者順序〔查證〕。頁面的列表(`GET /skill-hub/entries?q=`)
用同一條規則〔查證〕。

user 要的方向:分組——整句符合、單字符合,最後依使用量之類的排序〔user〕「我相信你應該要group, 現在的exact
match到單字match 最後是什麼使用量或是什麼排序什麼的」。

## 2. 決定

| # | 決定 | 來源 |
|---|---|---|
| D1 | **三組**,依序:① 整句原封不動出現在名稱或描述(現在的規則);② 每個字詞都出現,順序不拘;③ 只有部分字詞出現,符合的字詞越多越前面。一個都沒有就不列 | 〔user〕(方向),〔施工〕(細節) |
| D2 | **組內依使用次數排**,同次數再看安裝次數,再同就照原本的名稱、擁有者順序 | 〔user〕選「使用次數多的先」 |
| D3 | 字詞:空白和標點分開;中日韓文字沒有空白,連續的一段切成相鄰兩字(「晶圓良率」→ 晶圓、圓良、良率),單一個字就是它自己。比對名稱和描述,不分大小寫 | 〔施工〕 |
| D4 | **頁面和 AI 共用同一條規則**(`skill_hub.py` 一個函式)——兩邊各寫一份、手動保持一致,以前就出過錯(`matches_query` 的註解)〔查證〕 | 〔user〕選「頁面也改」 |
| D5 | **頁面的排序選單多一個「最相關」**(D1 + D2),只在有查詢時出現;有查詢、又沒選過排序時預設就是它。名稱/最常用/最近更新照舊可選,只是比對範圍跟著變大 | 〔user〕選「有查詢時預設最相關」 |
| D6 | 查詢是空的:照舊列出全部、照原本順序;頁面的「最相關」在沒有查詢時等於依名稱 | 〔施工〕 |
| D7 | fork 照舊收在原作底下,跟著原作排(`nest_forks` 保留傳入的順序)〔查證〕 | 〔施工〕 |
| D8 | AI 的工具說明改寫:字詞順序不拘、最符合的在前、同樣符合時常用的在前——讓 AI 知道多寫幾個關鍵字不會害它找不到 | 〔施工〕 |

## 3. 不做

- 比對 SKILL.md 的內文(只比名稱和描述,和現在一樣)。
- 同義詞、錯字容忍、向量搜尋。

## 4. UI 依據

- NN/g「Intranet-Search Essentials」(https://www.nngroup.com/articles/intranet-search/):
  「Content on intranet search-results pages should rank based on relevance and recency」;排序用「A simple dropdown」。
  頁面本來就是下拉選單,只多一個選項,控制項種類不變。
- NN/g「The Power of Defaults」(https://www.nngroup.com/articles/the-power-of-defaults/):
  「It's therefore important to select helpful defaults, rather than those based on the first letter of the alphabet」——
  現在有查詢時預設依名稱字母排,正是這句說的反例;有查詢時預設改成「最相關」。

## 5. 階段

| 階段 | 內容 | 驗收 |
|---|---|---|
| P1 | 比對規則(`skill_hub.py`):分組、字詞切法;AI 工具依組 → 使用次數排,改寫工具說明 | 完整的查詢找得到只符合部分字詞的 skill;整句符合的排最前;同組常用的在前 |
| P2 | 頁面列表路由:`sort=relevant` | 有查詢時依 D1 + D2;沒查詢時等於依名稱 |
| P3 | 前端排序選單:「最相關」只在有查詢時出現、預設選它 | 截圖對照 §4 |
| P4 | 文件:`migrations.md`(行為改變,沒有開關) | `tests/docs`、`mkdocs --strict` |
| P5 | web-demo → review(conformance / veracity / defect / regression)→ CI | 照 CLAUDE.md |
