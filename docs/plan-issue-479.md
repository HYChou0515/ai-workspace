# Plan: wiki 每日沉思 — 週期性統整層 (#479)

> 「wiki 需要每日沉思、重新整理 wiki 內的東西、把 general concepts/terms 取出
> (follow llm wiki)」。這是加在 #50 增量維護(fold)**之上**的一個**週期性統整
> (consolidation/synthesis)層** —— 不取代增量。增量負責**保鮮**(每次 ingest 局部
> 更新);沉思負責**整理提煉**(退一步讀整份 wiki → 去重/合併/拆分/提煉概念)。
>
> 心智模型的直接先例 = topic-hub 的 `→consolidate`(「讀現有記憶 → 改寫:去重/
> 合併/摘要/丟棄」,且「週期性是呼叫者的事」)。這裡把同一模型套到 KB prose wiki。

---

## 0 · 鎖定的決策(`/grill-me` Q1–Q9)

| # | 決策 | 結論 |
|---|---|---|
| Q1 定位 | 加層 vs 取代 | **加上去的統整層**,不動 #50 增量維護;職責分離(保鮮 vs 整理提煉) |
| Q2 觸發 | 節奏 | **固定每日、可設定時間**(照 `CodeRepoSweeper`/`kb.git.daily_sync`)+ 手動 **Reflect now**。**變動閘 v1 不做**(每天無條件跑;「只在有變動才跑」留旋鈕) |
| Q3 範圍 | 哪種 wiki | **只 prose wiki**(`use_wiki` 且 `git_url is None`);code wiki(#281,確定性重生)跳過 |
| Q4 產物 | 概念去哪 | **留在 wiki 內**:`/concepts/*.md` 概念頁 + wiki 內術語索引;**外部 `ContextCard` glossary 不碰**(#414 獨立供),避免兩 producer 打架 |
| Q5 動作 | 做什麼 | **①提煉概念 ②術語索引 ③合併重複頁 ④修 `[[wikilink]]`+index ⑤統整矛盾 ⑥拆過肥頁 全做**;⑦孤兒頁**只標記不刪**。刪除政策:**只有 content-preserving 合併才刪頁** |
| Q6 模型 | 怎麼跑 | **survey → plan → apply**(仿 #281 code_wiki:程式管控制流、LLM 只做有界逐單元合成)。`1 plan + N apply` 次 LLM;survey 0 LLM;列舉每頁強制覆蓋率 |
| Q7 安全 | 冪等 | 保守 planner + **確定性 write-suppression**(新內容與現有 diff,一樣就不寫);wiki **AI-owned**(只 `/clarifications`、`/corrections` 免動,pin marker 延後);拆併**門檻+遲滯**;**不做**快照回滾 |
| Q8 可觀測 | 看得到 | 動作紀錄寫 **`/reflections/<date>.md` 專區**(reflect 自有命名空間,fold 免動、survey 自跳、reader 可讀);重用 `WikiBuildState.phase`;全 collect streaming;**不做** canned check(改手動 dogfood 當 live 驗證) |
| Q9 FE | 露出 | **Reflect now** 按鈕(擺 Rebuild 旁)+ **上次沉思時間**;概念/日誌靠**現有唯讀 wiki 樹**瀏覽;進度加 reflect phase 標籤 |

---

## 1 · 架構

```
         ┌─ fold  (既有 #50,每次 ingest 局部增量) ── 保鮮
prose ───┤
 wiki    └─ reflect (新;每日 or 手動,整份 wiki 統整) ── 整理提煉
                    │
                    └─ WikiReflector.reflect(cid):
                         survey()  程式、0 LLM:列每頁(跳過保留區/reflections/WIKI.md/log.md)
                                   → 每頁確定性抽一行(heading + Sources: + [[links]]) → digest
                         plan()    1 次 streaming collect:digest → ReflectPlan(結構化 JSON)
                                   空計畫 = no-op(天然冪等);拆併有 threshold+hysteresis
                         apply()   程式 iterate 計畫,每動作有界 collect + 程式寫檔:
                                   - concept:提煉/刷新 /concepts/<slug>.md
                                   - merge:內容併進 target + 修 inbound [[link]] + 刪空頁(content-preserving)
                                   - split:過肥頁拆多頁
                                   - 修 index + 術語索引
                                   - orphan:只寫進 /reflections 日誌(不刪)
                                   全部經 write-suppression(diff 一樣不寫)
```

### 1.1 儲存與命名空間(P1)

- **`/reflections/`** 加進 `store._RESERVED_DIRS`(與 `/clarifications/`、`/corrections/` 同級)。
  效果:fold/unfold/correct maintainer(走 `MaintainerWikiStore`)**動不了** `/reflections/`;
  反思**日誌用 raw `WikiFileStore` 寫**(比照 corrections/clarifications landing path 用 raw store 的既有模式)。
- **頁面重整走 `MaintainerWikiStore`**(guarded)→ 自動保住 `/clarifications`、`/corrections`(Q7)。
- **`Collection.last_reflected_at: str = ""`**(非索引,無 migration;比照 `auto_digest` 等既有欄位)。
  reflect 完成時 stamp ISO 時間 → FE 顯示「上次沉思」。

### 1.2 WikiReflector(P2–P4,新模組 `kb/wiki/reflect.py`)

仿 `CodeWikiBuilder`:`__init__(spec, llm, *, wiki_store=None)`;prompt 用 inline 常數(仿 `kb/quality.py`/code_wiki,非 .md);LLM = `ILlm.collect()`(底層永遠 stream)。

- **survey**:`WikiFileStore._paths`(metadata-only,#411)列頁,skip `_is_reserved` + `/reflections/` + `/WIKI.md` + `/log.md`;每頁讀一次,`_first_paragraph_after_h1`(重用 code_wiki)抽一行 + parse `Sources:` + `[[links]]` → digest(每頁一行,有界)。
- **plan**:`collect(plan_prompt)` → `_unfence` → `msgspec.json.decode(ReflectPlan)`;parse 失敗 → 空計畫(no-op,安全)。保守 prompt(只在有具體缺陷才提動作)。拆/併門檻(page size)+ hysteresis(兩門檻留 gap)。
- **apply**:程式 iterate;每動作聚焦 collect(輸入只有牽涉頁);寫檔前 **write-suppression**(讀舊內容,byte-equal 就 skip);merge 後修 inbound `[[link]]`(程式,deterministic)。

### 1.3 Coordinator 接線(P5)

- `WikiJobPayload.op` 增 `reflect`(`jobs.py` docstring 補一行)。
- `_handle` dispatch:`elif payload.op == "reflect": self._handle_reflect(payload, triggered_by=actor)`。
- producer `enqueue_reflect(cid, *, requested_by=None)`:僅 prose `use_wiki`(且非 git_url);coalesce(已有 active reflect 就跳);`partition_key=cid`(免費序列化,且與 fold 序列化 → 不同時跑)。
- `_handle_reflect`:seed phase=`surveying` → `WikiReflector.reflect(cid, on_phase=…)`(phase 依序 surveying/planning/applying,`current` 帶 N/M)→ 記錯不 crash partition(仿 `_handle_fold`);頁寫 `acting_as(actor)`(#83)。
- reflector 用既有 `code_wiki_llm`(= kb.wiki.llm,就是 wiki LLM):`self._reflector = WikiReflector(...) if code_wiki_llm is not None else None`(免動 `build_coordinators` LLM 接線)。

### 1.4 Route(P6)

- `POST /kb/collections/{id}/wiki/reflect` → `coordinator.enqueue_reflect(cid, requested_by=user)` + `start_consuming`;回 pydantic(照既有 rebuild route)。進度沿用 `GET .../wiki/status`。

### 1.5 排程(P7)

- `reflect_sweeper`(`api/lifecycle.py`,仿 `code_sync_sweeper`/`CodeRepoSweeper`):每日牆鐘於設定時間,對每個 prose `use_wiki` collection `enqueue_reflect`。
- config `settings.kb.wiki.reflect`(`enabled: bool`、`daily_time`/`interval`);沿用 `kb.git.daily_sync` 的形狀。非 queue sweeper 永遠留 API(不受 `run_consumers` gate)。

### 1.6 FE(P8)

- **Reflect now** 按鈕擺 wiki 分頁 Rebuild 旁 → `POST .../wiki/reflect`;顯示 `last_reflected_at`。
- reflect phase 標籤(`surveying/planning/applying`)進現有 `wiki/status` 進度 UI(仿 code wiki phase 標籤)。
- 概念(`/concepts/`)、日誌(`/reflections/`)自動出現在現有唯讀 wiki 樹,v1 無專屬視圖。

---

## 2 · 扁平階段(flat integer;每 phase 一 commit)

1. **P1** `/reflections/` 命名空間(reserved dir,raw-store 可寫、maintainer 免動、survey 跳過)+ `Collection.last_reflected_at`。
2. **P2** `WikiReflector.survey` — 確定性 digest builder(純函式)。
3. **P3** `ReflectPlan` schema + `WikiReflector.plan`(streaming collect,保守 prompt,拆併 threshold+hysteresis,tolerant parse → 空計畫)。
4. **P4** `WikiReflector.apply` — 各動作 executor(concept / merge+link-fix+content-preserving delete / split / index+術語索引 / orphan→journal)+ write-suppression;`reflect()` 串起 survey→plan→apply + `on_phase`。
5. **P5** Coordinator:`op="reflect"` + `_handle_reflect` + `enqueue_reflect` + reflector 注入。
6. **P6** `POST .../wiki/reflect` route(手動)。
7. **P7** `reflect_sweeper`(每日)+ `kb.wiki.reflect` config。
8. **P8** FE:Reflect now 按鈕 + 上次沉思時間 + reflect phase 標籤(vitest TDD)。
9. **P9** live dogfood(手動 Reflect now 對真 collection 跑真 LLM)+ docs(`development.md` how-to)。

DoD:每 phase `ruff check && ruff format --check`、`ty check`、targeted `pytest` 綠、commit;最後全套 `coverage … --fail-under=100` + FE `typecheck`+`build`。

---

## 3 · 不在範圍(本次)

- **變動閘**(「只在 wiki 有變動才跑」的排程優化)—— Q2 明確延後為旋鈕。
- **pin marker**(保護人工手改的 prose wiki 頁)—— Q7 延後;v1 wiki 視為 AI-owned。
- **概念 → 外部 `ContextCard` glossary 橋接** —— Q4 留 follow-up(重用 `classify_against_existing` 去重)。
- **超大 wiki 的 plan fan-out 分批**(digest 上千頁才需要)—— 同 code_wiki 當初,延後。
- **快照 / 回滾** —— Q7 延後;靠 content-preserving 合併 + write-suppression + 日誌。
- **canned capability check** —— Q8 user 明確 waive;改手動 dogfood 當 live 驗證。

## 4 · 風險與對策

- **每日無條件跑 → thrash**:write-suppression(byte-diff)保證穩定 wiki 實體不動;保守 planner 回空計畫;拆併 hysteresis 防震盪。代價 = 穩定 wiki 每日仍白跑 `1 plan + N apply` LLM(變動閘補上即免)。
- **#50 narrate 雷**:apply 由**程式**寫檔(非 agent loop),繞開。
- **context 爆**:survey digest 每頁一行(有界),plan 只讀 digest,apply 每動作只讀牽涉頁。
- **誤刪**:唯一刪來自 content-preserving 合併(內容已併入 target);孤兒只標記;guarded store 擋掉 ground-truth 保留區。
