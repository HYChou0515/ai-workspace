# Skills + Tool packages — issues #29 + #25

> Status: 草稿(grill 已完成,等用戶 review 後進 /tdd)。
> 投資人: hychour。
> 來源:
> - §A: issue #29「支援 skill」+ 9 題 grill。
> - §B: issue #25「prebuild_tools.py 太複雜,例如使用 typer 多 command in one package」+ 10 題 grill。

兩個 feature 同一份 plan(用戶意願),但實作仍走 vertical slice — `/tdd` 一個 commit
一個 slice,可挑任一段先動。決策樹獨立、沒交集:§A 是 host-side markdown 機制,
§B 改 sandbox tool 的 invoke contract;唯一接觸點是兩者都動 `agent/tools.py` 的 tool
註冊路徑,合到 §C(整合期)再 reconcile。

---

# §A · Skills (issue #29 — RCA progressive disclosure)

## A.0 · 為什麼

CLAUDE 端我們重度倚賴 Anthropic Skills:`.claude/skills/{diagnose,triage,…}/SKILL.md`,
靠 progressive disclosure 把方法論知識留在外部、按需展開到 context。

自家 RCA agent 目前沒有同類機制:`compose_system_prompt(base, profile)` 一次把
所有 template 知識塞進 system prompt,塞越多越貴 + 方法論散落 `_prompt.md`。

issue #29 要的是讓自家 agent 也走 progressive disclosure。範圍綁在 **RCA template**
(每 template profile 自帶一組 skill,沒有全域池)。

## A.1 · 決策表(grill 收斂)

| # | 問 | 答 |
|---|---|---|
| Q1 | `_prompt.md` 跟 skill 的職責 | `_prompt.md` 講「workspace 有什麼檔」,skill 講「怎麼做某件事」,不重疊 |
| Q2 | skill 在 template 怎麼擺 | `templates/<profile>/.skill/<name>/SKILL.md` 資料夾結構 |
| Q3 | sandbox 內要不要 `/.skill/` | 不要,純 host-side(避開動 sandbox protocol) |
| Q4 | frontmatter 欄位 | 只 `name` + `description`(對齊 Anthropic) |
| Q5 | tool 三件套 | `read_skill(name) -> str`;找不到 → error 字串 + 列可用;不另加 list_skills tool |
| Q6 | 沒 skill 的 template 怎麼處理 | skill index + tool **同進同退** |
| Q7 | body 長度上限 | 不套 read_file 截斷;sanity hard cap 50k chars,超過 reject |
| Q8 | cache 策略 | `functools.cache`,server restart 才 refresh |
| Q9 | KB chat 吃不吃 skill | 不吃,留 P3.x |

## A.2 · 範圍(v1)

### A.2.1 檔案佈局

```
src/workspace_app/rca/templates/<profile>/.skill/<name>/SKILL.md
```

frontmatter(YAML):
```yaml
---
name: 5-why-walkthrough
description: 引導用戶完成 5 Whys。當用戶說「為什麼」/「root cause」/ 已開好 /5-why.md 但內容空白時使用。
---

# 5 Whys 流程
(body markdown — 真正方法論在這裡)
```

### A.2.2 新模組

`src/workspace_app/rca/skills.py`:
- `SkillMeta(Struct, frozen=True) { name: str, description: str }`
- `class SkillError(Exception)`
- `@cache list_skills(profile: str) -> list[SkillMeta]` 依名稱排序
- `@cache load_skill(profile: str, name: str) -> str` body markdown,frontmatter strip
- `_parse_frontmatter(raw: bytes) -> (dict, str)` `---` ... `---` YAML
- 常數 `SKILL_BODY_CAP = 50_000`

### A.2.3 既有模組改動

- `rca/templates/__init__.py::compose_system_prompt` 末尾,當 `list_skills(profile)` 非空時追加 skill index 段。
- `agent/tools.py` 加 `read_skill_impl`(host-side,不喚醒 sandbox);加進 `_IMPLS` 但不進 `_WORKSPACE_TOOLS` 預設集;`build_tools(allowed, profile=None)` 當 profile 給 + skill 非空時 append `read_skill`。
- `agent/context.py::AgentToolContext` 加 `template_profile: str | None = None`。
- `api/litellm_runner.py` 傳 template_profile 進 build_tools + AgentToolContext。
- `api/app.py::_resolve_agent_config` 把 template 一路串到 runner。

### A.2.4 Skill index 注入流程

```
user 送訊息
  → _resolve_agent_config(inv) → cfg.system_prompt = compose_system_prompt(base, template)
                                                       ↓ 末尾追加 skill index(若 list_skills(template) 非空)
  → LitellmAgentRunner.run(cfg, tools=build_tools(cfg.allowed_tools, profile=template), context=…)
                                                       ↓ 若 list_skills(template) 非空 → append read_skill tool
  → agent 看 prompt → call read_skill("5-why-walkthrough")
                                                       ↓ host-side read templates/<template>/.skill/5-why-walkthrough/SKILL.md
                                                       ↓ strip frontmatter,回 body markdown
  → agent 套用方法論回答
```

### A.2.5 Seed skills(示範)

在 `methodology` template 下放三個示範 skill:
```
src/workspace_app/rca/templates/methodology/.skill/
  ├─ 5-why-walkthrough/SKILL.md       # 引導 5 Whys
  ├─ fishbone-6m/SKILL.md             # 6M fishbone 分類
  └─ stop-the-line-checklist/SKILL.md # 何時停線
```

### A.2.6 Test plan

`tests/rca/test_skills.py`:
- `test_skillmeta_parsed_from_frontmatter`
- `test_list_skills_returns_alphabetical_meta_for_template_with_skills`
- `test_list_skills_returns_empty_for_template_without_skills`
- `test_load_skill_strips_frontmatter_returns_body`
- `test_load_skill_unknown_name_raises_skill_error`
- `test_load_skill_body_exceeding_cap_raises_skill_error`
- `test_frontmatter_missing_name_skips_skill_with_warning`
- `test_frontmatter_malformed_yaml_skips_skill_with_warning`
- `test_skill_dir_name_mismatch_with_frontmatter_name_skips_with_warning`

`tests/rca/test_templates_skill_index.py`:
- `test_compose_system_prompt_appends_skill_index_when_template_has_skills`
- `test_compose_system_prompt_omits_skill_section_when_template_has_no_skills`
- `test_skill_index_uses_alphabetical_order`

`tests/agent/test_read_skill_tool.py`:
- `test_read_skill_returns_body_for_known_skill`
- `test_read_skill_returns_error_string_listing_available_for_unknown`
- `test_read_skill_uses_template_profile_from_context`
- `test_read_skill_does_not_wake_sandbox`

`tests/api/test_turn_with_skills.py`(端對端):
- `test_turn_with_methodology_template_exposes_read_skill_tool`
- `test_turn_with_default_template_does_not_expose_read_skill_tool`
- `test_turn_agent_can_call_read_skill_via_scripted_runner`

### A.2.7 階段

| # | 內容 | 狀態 |
|---|---|---|
| **S1** | grill + plan(本份) | ✅ |
| **S2** | `rca/skills.py`(SkillMeta + list_skills + load_skill + frontmatter parse + cap)+ 單元測試 | ⬜ |
| **S3** | `compose_system_prompt` 串 skill index + 測試 | ⬜ |
| **S4** | `read_skill` tool + `AgentToolContext.template_profile` + `build_tools(profile=)` + 整合測試 | ⬜ |
| **S5** | runner / app.py 把 template_profile 一路串到 ToolContext + 端對端測試 | ⬜ |
| **S6** | seed `methodology/.skill/{5-why-walkthrough, fishbone-6m, stop-the-line-checklist}/SKILL.md` | ⬜ |
| **S7** | 100% coverage / ty / ruff | ⬜ |

## A.3 · 不做(v1 之外)

- 全域 / 跨 template skill 池
- skill 鋪進 sandbox `/.skill/`
- KB chat 吃 skill(留 P3.x)
- skill 互引用自動展開
- skill `$placeholder` substitute
- skill version / 變更歷史
- FE 線上編輯 skill

---

# §B · Tool packages(issue #25 — JSON contract + 多 command + 共享 venv)

## B.0 · 為什麼

現況痛點(`agent/provision.py` + `rca/sample_tools.py` + `scripts/prebuild_tools.py`):

- **一 tool = 一 package**:`data-fetch` 跟 `csv-column-summary` 各自一份 .venv + 一份 portable python(~150MB / 個),即使 deps 90% 重複。
- **argv 拆解的 schema 受限**:`build_argv` 把 LLM 給的 JSON dict 轉成 argv positional + `--flag value`。**沒辦法表達 list of str / nested object**;bool 還要靠 schema 反推。
- **作者寫 tool 要兼顧 argparse 解析**,跟 LLM 給的 JSON shape 不對齊。
- **prebuild 邏輯散在三處**:`SOURCES` dict、`build_one()`、`ToolDef` schema。

## B.1 · 決策表(grill 收斂)

| # | 問 | 答 |
|---|---|---|
| T1 | backend → sandbox JSON args 怎麼傳 | argv `[launch, <command>, '<json>']` 三件;**零侵入 sandbox protocol** |
| T2 | LLM 看的 params JSON schema 從哪來 | tool 自我描述 + prebuild 階段固化成檔(`commands.json` + `schemas/<sub>.json`) |
| T3 | host 端 registry 怎麼寫 | `PACKAGES = {pkg_name: source_path}` thin dict,其他全從 prebuilt 推 |
| T4 | allowed_tools 配置端格式 | colon 分隔:`"datalab"` 全收 / `"datalab:summarise"` 細選 |
| T5 | LLM 看的 tool name | 扁平 command name(`summarise`,LLM 看不到 package);撞名 host startup raise |
| T6 | tool 回傳 | stdout / stderr / exit_code 三件套(沿用既有 `_format_exec`) |
| T7 | 既有 ToolDef + build_argv | **完全廢棄**;`provision.py` 重寫 |
| T8 | prebuild script 簡化 | 大改 + 模組化(`workspace_app/tooling/{prebuild,dispatcher,registry}.py`)+ 增量(source mtime 比對 skip) |
| T9 | sample tool 遷移 | `data-fetch` 留單 command(範例:最小可行);`csv-column-summary` 變多 command(`summarise` + `plot`,共享 venv) |
| T10 | pydantic ValidationError 回 LLM | friendly str 印 stderr + exit_code=2;host 透傳 |

## B.2 · Binary contract(T1 + T2)

每個 package 的 launcher binary 服從**三段 contract**:

```
$ ./launch                          # 零參:列所有 commands(JSON array)
[
  {"name": "summarise", "description": "..."},
  {"name": "plot",      "description": "..."}
]

$ ./launch summarise                # 一參:該 command metadata + JSON schema
{
  "name": "summarise",
  "description": "Summarise each column of a CSV ...",
  "params_json_schema": { "type": "object", "properties": {...}, "required": [...] }
}

$ ./launch summarise '{"csv":"x.csv"}'   # 兩參:執行
# stdout / stderr / exit_code 跟既有 tool 一樣
```

**Tool 端寫法**(我們不強制 typer;framework 不挑):
```python
# sample-tools/csv-column-summary/src/csv_column_summary/cli.py
import json, sys
from pydantic import BaseModel, ValidationError
from .commands import summarise, plot

COMMANDS = {"summarise": summarise, "plot": plot}

def main() -> None:
    if len(sys.argv) == 1:
        print(json.dumps([
            {"name": n, "description": c.DESCRIPTION} for n, c in COMMANDS.items()
        ]))
        return
    cmd_name = sys.argv[1]
    cmd = COMMANDS.get(cmd_name)
    if cmd is None:
        print(f"unknown command: {cmd_name}", file=sys.stderr); sys.exit(2)
    if len(sys.argv) == 2:
        print(json.dumps({
            "name": cmd_name,
            "description": cmd.DESCRIPTION,
            "params_json_schema": cmd.Args.model_json_schema(),
        })); return
    try:
        args = cmd.Args.model_validate_json(sys.argv[2])
    except ValidationError as e:
        print(str(e), file=sys.stderr); sys.exit(2)
    cmd.run(args)
```

framework 提供 helper:`workspace_app/tooling/dispatcher.py`,作者寫法更短(decorator-based)。Helper 是 opt-in,作者要自寫 main() 也可以。

## B.3 · Host 端讀 schema(T2 + T3)

```python
# src/workspace_app/tooling/registry.py
def discover_packages(prebuilt_dir: Path) -> list[PackageInfo]:
    """從 PREBUILT_DIR/<pkg>/{commands.json, schemas/*.json} 構造 PackageInfo list。"""

def build_function_tools(
    packages: list[PackageInfo],
    allowed: list[str] | None,   # ["datalab", "csv-column-summary:plot"]
) -> list[FunctionTool]:
    """allowed 解析:colon 細選、純 pkg 全收;扁平 command name 撞名 raise。"""
```

`build_function_tools` 替代既有 `provision.build_provisioned_tools`。每個 FunctionTool 的 `on_invoke` 跑:
```python
result = await ctx.context.sandbox.exec(
    handle,
    [f"../.tools/{pkg}/launch", cmd_name, args_json],
    on_output=ctx.context.on_exec_output,
)
return _format_exec(result)
```

## B.4 · Prebuild 流程(T2 + T8)

```python
# src/workspace_app/tooling/prebuild.py
def build_package(name: str, source: Path, dst: Path) -> None:
    """1. uv venv --relocatable + uv pip install source
       2. .venv/bin/<name> → dst/commands.json
       3. for each cmd in commands: .venv/bin/<name> <cmd> → dst/schemas/<cmd>.json
       4. 複製 portable python + 寫 launch
       Idempotent:source dir mtime 比對 dst metadata,沒變則 skip。"""

# scripts/prebuild_tools.py(thin entry,~10 行)
from workspace_app.tooling.prebuild import build_package
from workspace_app.rca.tool_packages import PACKAGES, PREBUILT_DIR

def main():
    for name, source in PACKAGES.items():
        build_package(name, source, PREBUILT_DIR / name)
```

## B.5 · sample tool 遷移(T9)

`sample-tools/data-fetch/`(**保留單 command package 範例**):
- `pyproject.toml` `[project.scripts] data-fetch = "data_fetch.cli:main"`
- `src/data_fetch/cli.py` 三段 contract;只有一個 command `data-fetch`(零參時印 `[{"name":"data-fetch", ...}]`,一參跟兩參都接 `data-fetch`)

`sample-tools/csv-column-summary/`(**多 command 共享 venv 範例**):
- `pyproject.toml` `[project.scripts] csv-column-summary = "csv_column_summary.cli:main"`
- `src/csv_column_summary/{cli.py, commands/{summarise.py, plot.py}}`
- 原本 `plot=true` 參數拆成獨立 `plot` command
- 兩個 command 共享同個 .venv(pandas / numpy / matplotlib 一份)

`tool-demo` template `_config.json`:
```jsonc
{
  "allowed_tools": ["data-fetch", "csv-column-summary"]  // 兩 package 全收
}
```

或細選:`"csv-column-summary:plot"` 只開 plot。

## B.6 · 廢棄物(T7)

刪:
- `src/workspace_app/agent/provision.py::ToolDef`(整類)
- `src/workspace_app/agent/provision.py::build_argv`
- `src/workspace_app/agent/provision.py::build_provisioned_tools` / `_to_function_tool`(改成 `tooling/registry.py`)
- `src/workspace_app/rca/sample_tools.py::SOURCES` dict、`SAMPLE_TOOLS` list、`available_sample_tools()`(改成 `tool_packages.py::PACKAGES` + `tooling/registry.discover_packages`)

保留 + 改名(避免歷史拖累):
- `agent/provision.py::provision_tools(sandbox, handle, packages)` 保留 — 改參數型別,做的事(copy package archive → setup)邏輯不變。

## B.7 · Test plan

`tests/tooling/test_dispatcher.py`:
- `test_dispatcher_lists_commands_with_no_args`
- `test_dispatcher_prints_schema_for_known_command`
- `test_dispatcher_executes_command_with_validated_args`
- `test_dispatcher_returns_validation_error_on_bad_json`
- `test_dispatcher_unknown_command_exits_2`

`tests/tooling/test_prebuild.py`:
- `test_build_package_writes_commands_and_schemas`
- `test_build_package_skips_when_source_unchanged`
- `test_build_package_rebuilds_on_source_change`

`tests/tooling/test_registry.py`:
- `test_discover_packages_loads_commands_and_schemas_from_prebuilt`
- `test_build_function_tools_expands_pkg_to_all_commands`
- `test_build_function_tools_colon_filter_picks_single_command`
- `test_build_function_tools_raises_on_cross_package_name_collision`

`tests/agent/test_function_tool_invokes_via_argv_json.py`:
- `test_function_tool_passes_json_args_as_third_argv`
- `test_function_tool_routes_exec_through_sandbox`
- `test_function_tool_formats_exec_output_with_stdout_stderr_exit`

`tests/sample-tools/`(每 sample tool 各自 test):
- `data-fetch`:既有 unit tests(no change)+ 新增 cli contract tests
- `csv-column-summary`:既有 unit tests + 新增 cli contract tests(含 summarise + plot 兩 command)

## B.8 · 階段

| # | 內容 | 狀態 |
|---|---|---|
| **T1** | grill + plan(本份) | ✅ |
| **T2** | `tooling/dispatcher.py`(helper module + 三段 contract 抽出) + 單元測試 | ⬜ |
| **T3** | `tooling/prebuild.py`(build_package + dump schemas + 增量) + 單元測試 | ⬜ |
| **T4** | `tooling/registry.py`(discover_packages + build_function_tools + 撞名偵測) + 單元測試 | ⬜ |
| **T5** | `provision.py` 重寫:drop ToolDef / build_argv;`provision_tools(sandbox, handle, packages)` 改新參數型別 | ⬜ |
| **T6** | `rca/tool_packages.py`(取代 sample_tools.py)+ `PACKAGES` dict + `__main__` 串接 | ⬜ |
| **T7** | migrate `sample-tools/data-fetch`(單 command contract) | ⬜ |
| **T8** | migrate `sample-tools/csv-column-summary`(多 command:summarise + plot,共享 venv) | ⬜ |
| **T9** | `tool-demo` template `_config.json` 改 `["data-fetch", "csv-column-summary"]`,端對端跑通 | ⬜ |
| **T10** | 100% coverage / ty / ruff | ⬜ |

## B.9 · 不做(v1 之外)

- nested object / oneOf / discriminated union 的 schema 範例(pydantic 已支援,作者愛用就用)
- tool 出 stream output 給 LLM(stdout 是行緩衝,既有 `on_exec_output` 已串接 SSE)
- env-based / stdin-based invoke contract(T1 已選 argv)
- 動態 hot-reload package(prebuild 跑完才能 serve;對齊 skill A.Q8 政策)
- 跨 sandbox 共享 mount cache(同 host 多 sandbox 自動共享,Linux page cache 處理)
- 多個 portable python 版本共存(一個 deployment 一個 python,簡單)
- typer 整合 framework(用戶可選 typer 寫自家 dispatcher;我們不 ship)
- FE「Add new tool package」流程(deploy-time 行為,跟 skill 同政策)
