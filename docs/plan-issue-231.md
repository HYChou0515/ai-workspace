# Plan — Issue #231: Diagnostics App (sanity check → coverage table + AI scoring)

> Status: grilled & locked (`/grill-me`), not yet built. Drive implementation with `/tdd`.

## What & why

Today the **sanity check** is a one-off 2D matrix (`question × reasoning_effort`, one model
at a time) on `DiagnosticsPage`/`SanityMatrix`. Cells are graded by **mechanical Python
predicates** (`is_valid_json`, `contains("台北")`, …) or left blank for the operator to
**eyeball** against `expected`. Pain points:

1. The axis people actually care about is **`question × models`** (which local model is fit
   for which role — KB chat / RCA / VLM / judge / reasoning on-off), not effort-vs-effort
   for a single model. Used to pick models for failover (#196).
2. Lots of cells have **no mechanical grader** → manual eyeballing.
3. **No visibility into coverage** — you can't see *which cells were never run* and still
   need filling.

#231 reframes sanity check into a **model-fitness workbench**:

- A **first-class Diagnostics page** (promoted in the launcher) built around **one global,
  sortable/filterable/groupable table**.
- The table is driven by the **full expected grid** so **never-run blanks are visible** and
  fillable in one click — this is the headline value.
- **AI helps with the final scoring**: an LLM judge grades **every** cell (alongside the
  mechanical grade) and produces a **per-model overall fitness verdict**.
- Questions become **user-authorable** (no code) so you can grow the suite / organise 題組.

## Locked decisions (from grill)

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Not** an `apps/<slug>/` platform App. It's a **launcher-promoted page** with **one global table**. No `Run`/WorkItem snapshots. | Sanity check has no per-item chat workspace; its natural shape is a single table. Existing `SanityResult` is already a global `(model, question, level)` upsert. |
| D2 | Primary view = **flat table**, columns: 題目 / 題組類型 / model / effort / 機械評分 / **ai評分** / **ai評語** / ai答案(=model output) / 參考答案 / aux (+ existing reasoned / latency / error as optional cols). 2D matrix is **removed**. | `question × models` comparison is achieved by sort/group, more flexible than a fixed grid. |
| D3 | **Coverage-driven rows.** The table enumerates the **full expected grid** `configured_models × questions × question.levels` and **left-joins** results. Each row has a **status: ⬜未跑 / ⏳排隊·跑中 / ✅完成 / ❌錯誤**. | A table of *existing* `SanityResult` rows can't show blanks (a never-run cell = no row). Must drive from the cartesian to surface gaps. |
| D4 | Coverage UX: **"只看未跑" filter**, **per-model coverage indicator** (`38/52`), **"跑掉所有未跑的"** button (one-click fill, respects current model/題組 filter), plus cell / row(題目) / column(model) re-run. | The core ask: "我需要知道哪些空白還沒跑過需要填上". |
| D5 | **AI judge grades every cell** (incl. cells that have a mechanical grade) → `ai_grade` (pass/fail, comparable to 機械評分) + `ai_note` (rationale). Disagreement between mechanical & AI is a useful signal. | Fills the eyeball blanks **and** cross-checks naive mechanical graders (substring match can pass a wrong answer). |
| D6 | **Per-model overall verdict**: AI reads all of a model's cells → `score` (0–100) + `summary` (markdown w/ per-role fitness). Shown as one card per model above the table. | The "最後評分" — a finishing fitness call so you don't read the whole matrix. |
| D7 | Judge trigger: **auto in the run pipeline** (each cell's output → judge right after mechanical grade) + a **"重新 AI 評分"** button to re-judge without re-running the models. | Table is always scored; re-judge is cheap vs re-running models. |
| D8 | Judge model: configured **`diagnostics.judge_llm`** (preset cascade like `kb.vlm_format_llm` → `resolve_llm_chain` → single `LitellmLlm` or busy-aware `FallbackLlm` from #196), **streaming**. `null` ⇒ AI scoring gracefully off (ai cols empty, no verdict). Judge is **not forced** to be a model under test. | Reuse existing LLM-resolution + failover plumbing; no self-grading. Mirrors other optional-LLM features. |
| D9 | **Question authoring** (feature 6): built-in 19 questions stay **in code** (they have Python graders, UI read-only). **Custom questions** = a new **specstar resource** with `prompt / expected / 題組 / levels`, **no mechanical grader → AI-only graded**. Full question list = built-ins ∪ custom. | UI can't author Python graders; AI-grading (D5) makes a grader unnecessary for custom questions. |
| D10 | **題組類型 = a string tag** on each question (the existing `category`), not a separate "set" entity. | Smallest thing that delivers grouping/filter/"run this 題組". |
| D11 | effort handling: each question runs at its declared **`auto_levels`** (custom questions: levels chosen at author time). effort is a **filterable column**, not a primary axis. | User demoted effort; keeps existing data complete. |

## Non-goals (explicitly out)

- The paused **reasoning-control health check** (separate `health/` feature, memory
  `project_reasoning_health_check.md`) — do **not** fold it in.
- Editing the built-in 19 questions / their Python graders via UI.
- A declarative grader DSL for custom questions (AI-graded instead — D9).
- Cross-time history / run snapshots / model-over-version trend (D1 — no `Run`).
- Changing the model-endpoint source (`_sanity_endpoints` stays the model universe).

## Data model

Extend the existing cell resource and add two small resources.

```python
# resources/sanity.py — SanityResult: ADD two fields (default empty → no migration needed;
# old rows just show empty AI cols until re-judged).
class SanityResult(Struct):
    model: str
    question_key: str
    level: str
    output: str = ""
    reasoned: bool = False
    grade: str = ""            # mechanical pass/fail (existing)
    ai_grade: str = ""         # NEW: AI judge pass/fail ("" = not judged)
    ai_note: str = ""          # NEW: AI judge rationale
    aux: str = ""
    error: str = ""
    latency_ms: int = 0

# NEW resource: user-authored questions (no grader → AI-only). Built-ins stay in code.
class CustomSanityQuestion(Struct):
    category: str              # 題組 tag
    prompt: str               # single user turn (multi-turn = advanced, later)
    expected: str             # 參考答案 fed to the judge
    levels: list[str]          # which efforts to run (subset of ALL_LEVELS)
    enabled: bool = True
# INDEXED_FIELDS = ["category", "enabled"]

# NEW resource: per-model overall verdict (D6), keyed by model.
class SanityVerdict(Struct):
    model: str                 # indexed; one verdict per model (upsert)
    score: int = 0             # 0–100
    summary: str = ""          # markdown, per-role fitness bullets
# INDEXED_FIELDS = ["model"]
```

`question_key` stays a hash of the prompt/messages, so editing a custom question naturally
invalidates its cells (existing behaviour).

## Backend

- **Question registry merge**: `/sanity/questions` meta returns built-ins **∪** enabled
  `CustomSanityQuestion`s (custom → `grade=None`, `aux=None`). `find_question`/`question_key`
  resolve across both. The full expected grid (D3) = `models × questions × question.levels`.
- **Judge** (`diagnostics.judge_llm`): new `factories.get_sanity_judge_llm(settings)` →
  `resolve_llm_chain` (single or `FallbackLlm`). Streaming `ILlm`; coordinator accumulates.
- **Per-cell judging** (D5/D7): in `SanityBatteryCoordinator`, after a cell's output +
  mechanical grade, call the judge `(prompt, expected, output) → pass/fail + note`, write
  `ai_grade`/`ai_note`. No judge configured ⇒ skip (empty).
- **Per-model verdict** (D6): after a model's battery drains (or on demand), judge reads the
  model's cells → upsert `SanityVerdict(model, score, summary)`.
- **Endpoints** (typed pydantic responses, per repo convention):
  - `POST /sanity/run` — accept **multiple models** + optional 題組/level scope; **fan out
    per-cell jobs** (reuse #227 per-unit job + CAS join, never a single big job).
  - `POST /sanity/run-missing` — enqueue only the **未跑** cells for the given model/題組 scope.
  - `POST /sanity/rescore` — re-judge existing cells (and/or refresh verdicts) **without**
    re-running the models.
  - `GET /sanity/verdicts` — per-model verdict cards.
  - Custom questions — specstar **auto-CRUD** routes for `CustomSanityQuestion` (no hand-rolled).
- **Streaming**: every judge/verdict LLM call streams (memory: always-stream).

## Frontend (`web/`, TDD with vitest)

- **New coverage table** replacing `SanityMatrix`/2D grid: rows = full expected grid
  (cartesian) left-joined with `/sanity/results` across **all** models; **status column**
  (⬜未跑/⏳/✅/❌); sort + group + filter by model / 題組 / effort / status; the columns of D2.
- **Coverage affordances** (D4): "只看未跑" filter, per-model `done/total` indicator,
  "跑掉所有未跑的" button (respects filter), cell / 題目-row / model-column re-run.
- **Run controls**: multi-select models (from configured list) + 題組/scope → run.
- **Per-model verdict cards** (D6) above the table + "重新 AI 評分" button (D7).
- **Question management panel** (D9): list (built-in read-only + custom editable) + add /
  edit / delete custom questions (prompt / 參考答案 / 題組 / levels).
- **Launcher/nav**: ensure Diagnostics is a first-class entry; remove the old 2D matrix page.
- New UI strings via the i18n util; user-facing copy describes action/outcome, no internals
  (no "reasoning_effort"/"think"); zh-TW + en.

## Config

`config.example.yaml` only (never the live `config.yaml`). Add `diagnostics.judge_llm`
(preset reference; `null` = AI scoring off) with a comment that it should be a capable model
distinct from the models under test, and that a preset with `fallbacks` becomes busy-aware.

## Phases (flat integers; each shippable, `/tdd` red-green-refactor)

- **P1 — Data model**: add `ai_grade`/`ai_note` to `SanityResult`; join `expected` /
  `category` / question text into the results API; typed pydantic response. No behaviour
  change (fields default empty). Tests: serialization + API shape.
- **P2 — AI per-cell judge**: `diagnostics.judge_llm` factory (streaming, failover via
  `resolve_llm_chain`); coordinator fills `ai_grade`/`ai_note` after each cell; `null` ⇒ off.
  Tests: scripted/fake-LLM judge fills cells; off-path leaves empty.
- **P3 — Per-model verdict**: `SanityVerdict` resource + generation (judge over a model's
  cells) + `GET /sanity/verdicts`; refresh after battery. Tests: verdict upsert + content.
- **P4 — Coverage run model**: `POST /sanity/run` multi-model + `POST /sanity/run-missing` +
  `POST /sanity/rescore`; per-cell fan-out (#227 pattern). Tests: only-missing enqueues
  blanks; rescore doesn't re-run models.
- **P5 — Custom questions**: `CustomSanityQuestion` resource + specstar auto-CRUD; merge into
  question registry/meta (AI-only graded); 題組 tag. Tests: custom question appears in grid,
  AI-graded, no mechanical grade.
- **P6 — FE coverage table**: cartesian rows + status (⬜未跑…) + filters/sort/group +
  columns (D2) replacing the 2D matrix; run controls (multi-select, scope, **跑掉所有未跑的**);
  cell/row/column re-run. vitest.
- **P7 — FE verdict cards**: per-model fitness cards + "重新 AI 評分" button.
- **P8 — FE question management**: built-in read-only list + custom CRUD panel.
- **P9 — Launcher/nav + cleanup**: promote Diagnostics; remove old grid page; i18n strings
  (zh-TW + en); typecheck + build.
- **P10 — Gate + live check**: full local suite + `coverage combine` + `--fail-under=100`;
  whole-project `ty`; ruff. **Live canned check** (memory: LLM features need live checks):
  against real Ollama, confirm the judge actually grades a cell and a `SanityVerdict`
  generates with sensible content (not just fake-LLM green).

## Definition of done

- 100% coverage gate (full local suite) green; `ruff` + whole-project `ty` clean; FE `tsc` +
  `vite build` + vitest green.
- Diagnostics is a launcher-first page showing the coverage table; **未跑 blanks are visible
  and fillable in one click**; AI fills `ai評分`/`ai評語` per cell; per-model verdict cards
  render; custom questions can be authored and are AI-graded.
- Live canned check passed (judge + verdict on real Ollama).
