# 第三方 tool 散布：作者跑自己的 CI，新 sandbox 自動帶上

> Issue: [#674](https://github.com/HYChou0515/ai-workspace/issues/674)。
> 狀態：**P1–P14 全部實作完成**（見 §7 每個 phase 的核對）。

**一句話**：讓不在我們 repo 裡的工具作者，把工具推上自己的 GitLab、跑我們提供的 CI，
**下一個開起來的 sandbox 就自動帶上新版**——我們這邊不改 code、不重新部署。

---

## 1. 現狀（已查證）

今天一支工具要進系統，得走四步，每一步都在**我們**這邊：

1. 在 `src/workspace_app/tooling/packages.py` 的 `PACKAGES` 靜態 dict 加一行（該檔 38-40 行）
2. 跑 `scripts/prebuild_tools.py` → 產出 bundle 到 `.workspace-tools/<name>/`
3. bundle 被烤進 `sandbox-host` image（`sandbox-host/Dockerfile` stage 1 → stage 2）
4. 重新部署

也就是說：**工具作者需要我們 repo 的權限，而且每次改都要等我們發版。**

另外兩件跟這件事直接相關的現況：

- **工具的介面是 app 開機時的一張快照。** `discover_packages` 只在開機跑一次
  （`src/workspace_app/__main__.py:170`），結果進記憶體；每個 turn 由
  `build_function_tools` 從那份快照挑（`api/litellm_runner.py:325`），name/description/
  JSON schema 還會被 render 進 system prompt。
- **`.tools` 有兩種掛法。** unjailed 是 `create` 時一條指向整個 tools 目錄的 symlink
  （`sandbox-host/src/sandbox_host/local_process.py:277`）；jailed（生產）是每次 exec 的
  bootstrap 做 `mount --bind "$SANDBOX_TOOLS_DIR" "$ROOT/.tools"` + `remount,bind,ro`
  （同檔 59-60 行）。

---

## 2. 決策表（2026-07-24 + 2026-07-29 兩輪 grill 收斂）

| # | 問 | 答 |
|---|---|---|
| Q1 | 作者交出來的是原始碼還是產物？ | **build 好的產物**。作者自己在 CI/CD 跑我們提供的 build，平台不 build 陌生碼 |
| Q2 | 我們提供給作者的是什麼？ | 一個 **builder image**（= sandbox runtime base + build 腳本），不是裸腳本 |
| Q3 | artifact 長什麼樣？ | 兩個檔：`tool.tar.gz`（bundle）+ `tool.manifest.json`。放 GitLab CI artifact |
| Q3b | 為什麼是 gz 不是原訂的 zstd？ | **實作時改的**（P2）：要解壓的是 `sandbox-host`——它的 pyproject 明寫「deliberately minimal，安全敏感的 root 服務，與 app 零共用相依」。為了壓縮率塞一個 C extension 進去解**第三方的不可信 bytes**，換來的只是每個 (host, sha) 一次、幾秒的差別 |
| Q4 | manifest 欄位 | `format_version` / `name` / `version`（人類可讀，純顯示）/ `commands`（含 schema）/ `builder`（ABI 錨）/ `python`+`arch` / `bundle.sha256` / `source`（git+sha，純溯源） |
| Q5 | 「能跑的目錄」放哪？ | **host 本機磁碟**。共享 NFS 放 `/opt/tools` 已被否決——NFS 不能設權限，`root:root 755` 守不住 |
| Q6 | 註冊表放哪一層？ | **app.json**。不做 runtime registry、不做 admin UI |
| Q7 | 什麼時候查 GitLab？ | **開 sandbox 時**（實作上再往前一格到 turn 起點，見 Q9） |
| Q8 | `/opt/tools` 的 layout | **content-addressed**（`ext/<sha>/`）。`.tools` 那層把 sha **還原成 tool name**，工具端零改動 |
| Q9 | app 怎麼拿到 schema？ | **問 host**（新的 resolve 端點）。GitLab 憑證只在 host 一處 |
| Q10 | 工具的名字誰說了算？ | **我們**：`app.json` 的 key 是本地名；manifest 的 `name` 只當校驗 |
| Q11 | 新增一支工具時 name+url 填哪一層？ | **`app.json`**（改 repo + 發版）。不是部署設定、也不是線上 UI |
| Q12 | 第三方支不支援 `pkg:cmd` 只授權單一 command？ | **支援，且零成本**——第三方一旦成為 `PackageInfo`，`build_function_tools` 的展開邏輯與來源無關 |
| Q13 | private GitLab 的 token 怎麼給？ | **先一個全域 token**（host env）。per-project token 留待有需求再說 |
| Q14 | 要不要人類可讀的版本號？ | **要**：manifest 帶 `version`（取自作者的 `pyproject`）。**不參與信任**（信任錨仍是 sha），純粹讓「他跑的是 1.4.2」比一串 sha 好溝通 |
| Q15 | 要不要規定作者附測試？ | **不強制他們的單元測試**（我們管不到、也不該管），但 **`smoke` 是 build 的一部分：不過就不產出 artifact**。這是唯一有牙齒的那條 |
| Q16 | 上架前平台要不要先驗一次？ | **要，但是人工執行的指令**（`python -m workspace_app.tooling.verify <url> --name <本地名>`），不是自動閘。貼進 `app.json` 之前跑它 |
| Q16b | verify 要不要真的把 bundle 跑起來？ | **不要**（實作時改的，P9）：作者的 build 已經在**正確的 base** 裡強制跑過 smoke（Q15），而在維運者的機器上執行陌生人的程式碼是錯的地方、錯的環境、學到的還更少。verify 改做「抓 + 閘門 + 結構比對（bundle 內容是否與 manifest 一致）」 |
| Q17 | app 開機要不要 resolve 第三方、當 readiness 條件？ | **不要**。開機只做 best-effort 預熱；GitLab 掛掉不能讓 app 起不來（跟第一方 `discover_packages` 的 fail-loud 刻意不同，`__main__.py:149`） |
| Q18 | bundle 要不要設體積上限？逃生門長什麼樣？ | **要**：壓縮後 150MB（等於作者文件一直在講的數字）。逃生門是**平台簽發的憑證**：線下 review 後簽一行，內含 tool id / 放寬到多少 / 到期日（或 `never`）。作者端與上架閘門跑同一份規則；憑證跟著 manifest 走。**代價**：憑證離線驗章，發出去在到期前收不回來，要全面失效只能輪替金鑰。**只管第三方**：第一方 `sample-tools`（`scripts/prebuild_tools.py` 直接呼叫 `build_package`，不經過 `build_artifact`）刻意不納入——這是決定，不是漏掉 |

### Q2 為什麼一定要 builder image，不能是裸腳本

bundle 裡是 portable venv + portable python，而 numpy/pandas 這類帶原生 wheel 的東西**綁死
glibc 版本與 CPU 架構**。作者在自己 CI 的 base image build 出來的 bundle，搬進我們的 sandbox
會在**執行期**壞掉——「作者本機好好的、你這邊一跑就 GLIBC not found」是最難查的壞法。

builder image = **sandbox 實際執行的那個 image 的 base**，所以 ABI 相容是建構上保證的。
注意錨點是 `sandbox-host/Dockerfile` 的 **stage 2（host runtime）**，不是 stage 1——因為
host「jails processes INSIDE this image and ignores `SandboxSpec.image`」（該 Dockerfile 開頭
註解）。今天兩個 stage 剛好同 base（`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`），
但那是巧合，不能當保證。

### Q9 為什麼 schema 一定要跟 bundle 同源

若 app 用開機快照的 schema、sandbox 用開 sandbox 時抓的 bundle，兩者會**走鐘**：

> 作者週二把 `summarise` 的參數從 `column` 改成 `columns`。app pod 上週就沒重啟過 →
> agent 照舊 schema 送 `column`；sandbox 裡掛的是新 bundle，只認 `columns`。
> 使用者看到「工具一直報參數錯」，而 app 沒改、作者那邊測都是好的。

解法是把 **resolve 的結果在整個 turn 內釘住**：同一次 resolve 同時決定
「app 用哪份 schema」和「sandbox 掛哪個 sha」，兩者必定同源。

### Q10 為什麼名字由我們定

manifest 的 `name` 若是權威，兩個作者都叫 `data-fetch` 就無解，有人宣告成 `sci-plot`
還會蓋掉第一方。而 `_check_collisions` 是**每個 turn** 在選中的集合上 raise
（`tooling/registry.py:173,219`）——撞名會變成「使用者送訊息時才炸，而觸發的人是別人的 CI」。

所以：`app.json` 的 key = 本地名（我們仲裁），manifest 的 `name` 只用來校驗「我抓到的是不是
我以為的那支」，對不上就拒絕。

---

## 3. 作者面：一個 tool provider 實際拿到什麼

**這是本案的主產出**——平台那半（§4）是為了讓這半成立。

### 3.1 作者的 repo 長什麼樣

跟今天的 `sample-tools/*` **一模一樣**，作者不用學新結構：

```
my-wafer-tools/
  pyproject.toml          # [project.scripts] wafer-history = "wafer_tools.cli:main"
  uv.lock                 # 依賴釘死 —— bundle 可重現的前提
  src/wafer_tools/
    cli.py                # 3-stage 契約
    commands/…
  .gitlab-ci.yml          # ← 我們提供的範本，貼上就好
```

3-stage 契約（既有，`docs/extending-the-platform.md` §Tool 已有完整說明）：

| 呼叫 | 印出 |
|---|---|
| `launch` | `[{name, description}, …]` —— 這個 package 有哪些 command |
| `launch <cmd>` | `{name, description, params_json_schema}` —— 該 command 的 metadata |
| `launch <cmd> '<json>'` | 真正執行，結果吐 stdout |

**作者不用手寫 JSON schema**：builder 直接跑他的 CLI 把 schema 抽出來，跟今天
`prebuild` 固化 `commands.json` / `schemas/<cmd>.json` 是同一段程式。

### 3.2 `.gitlab-ci.yml`（我們提供的範本）

```yaml
build-tool:
  image: registry.example/ai-workspace/tool-builder:2026.07
  script:
    - build-tool /src dist          # builder image 內建的唯一指令
  artifacts:
    paths: [dist/]
    expire_in: never                # ← 必須。預設會過期，過期後 URL 直接 404
```

產出兩個檔：

```
dist/tool.tar.gz         # 會跑的整包：.venv/ + python/ + launch + commands.json + schemas/
dist/tool.manifest.json  # name / commands(含 schema) / builder / python+arch / bundle.sha256 / source
```

### 3.3 作者不推 CI 也能先自測

```sh
docker run --rm -v "$PWD:/src"   tool-builder:2026.07 build-tool /src dist
docker run --rm -v "$PWD/dist:/d" tool-builder:2026.07 smoke /d
```

`smoke` 在**真正的 sandbox base image** 裡把 bundle 解開、跑一遍 3-stage 契約，
所以「我這邊會動」跟「平台上會動」是同一件事，不是碰運氣。

### 3.4 作者交給我們的，就一串 URL

GitLab 的 latest-artifact 端點——**作者推一版，這串就自動指到新的**：

```
https://gitlab.example/api/v4/projects/<id>/jobs/artifacts/<ref>/raw/dist/tool.manifest.json?job=build-tool
```

平台把 `tool.manifest.json` 換成 `tool.tar.gz` 就是 bundle 的位址，所以**只需要一串**。

### 3.5 `docs/tool-authoring.md`（新增）

就是把 3.1–3.4 寫清楚，外加：

- manifest 每個欄位的意思，以及**什麼會讓平台拒絕你的 artifact**（`format_version` 不認得 /
  `builder` 對不上 / `arch` 不符 / sha 不符 / manifest 的 `name` 跟平台登記的對不上）
- 工具能拿到哪些環境變數（接 #669）
- §3.6 的「強制 vs 建議」兩張表，以及 §3.7 的上架流程

### 3.6 平台**強制**什麼、只是**建議**什麼

分清楚這兩欄很重要：強制的那些要有程式擋，建議的那些只能寫進文件——把建議寫成「規定」
而沒有東西擋，等於沒說。

**強制（不符就拒絕，或就是會發生）**

| | 怎麼擋 |
|---|---|
| 3-stage CLI 契約 | `build-tool` 抽不出 `commands.json` / `schemas/` 就 build 失敗 |
| `uv.lock` 存在 | 沒有就沒有可重現的 bundle，build 失敗 |
| **smoke 通過** | build 的最後一步；不過就**不產出 artifact**（Q15） |
| manifest 欄位齊、`name` 與平台登記的一致、`builder`/`arch` 相容 | resolve 的閘門（P1 驗證器） |
| **工具輸出會被截斷** | 不是拒絕，是既成事實：每個 FunctionTool 都過 `cap_tool_outputs`（`tooling/registry.py:178`） |
| **執行有時間上限** | host 預設 total 60s、idle 60s（`sandbox-host/.../local_process.py:223-224`） |

**建議（我們不擋，但作者不知道會踩雷）**

- **command 與參數的 description 要寫好**——agent 用不用得對、會不會亂填參數，幾乎只取決於這個。
  它們會被原樣 render 進 system prompt。
- **改 command 名、或加一個必填參數 = breaking**，而且對**所有新 sandbox 立即生效**（R4）。
  要改就發一個新 name，或先跟我們講。
- **不要把 secret 寫進 bundle**：同一台 host 上多個 sandbox 共用同一份。

### 3.7 上架 / 更新的流程（誰做什麼）

| 場合 | 誰 | 做什麼 |
|---|---|---|
| **新增一支工具** | 作者 | 寫工具 → CI → 給我們一串 manifest URL |
| | 我們 | `verify <url>` 跑過 → 貼進 `app.json` → 發版 |
| **作者發新版** | 作者 | push 就好 |
| | 我們 | **什麼都不用做**（url 不變，下一個 sandbox 自動帶上） |
| **出事要退回** | 我們 | `app.json` 的 url 換成 job-pinned（§4.4），或請作者回退 |
| **移除一支工具** | 我們 | 從 `app.json` 拿掉 → 發版；host cache 由 GC 回收 |

`verify` 做的事：跑 P1 的閘門 → 抓 bundle → 驗 sha → **在一個丟棄式 sandbox 裡跑一遍 smoke** →
回報「這支能不能上、哪裡不合」。它是**人工指令不是自動閘**（Q16）——目的是讓「貼進 app.json
再發版」之前就知道會不會爛，而不是部署完才發現。

---

## 4. 平台面

### 4.1 一次 turn 的資料流

```
turn 開始
  ├─ app 讀 app.json:  tools: [...第一方...],  external_tools: { 本地名 → artifact url }
  ├─ app → host   POST /tools/resolve  { tools: { name: url } }
  │     host 對每支：抓 manifest → 相容性閘門(format_version / builder / arch / name)
  │                 → cache 命中(by sha)就跳過下載
  │                 → 否則抓 tar → 驗 sha → 安全解壓進 /opt/tools/ext/<sha> (root:root 755)
  │     host ← 回  { name: { sha, commands, schemas, stale? } }
  ├─ app 把 commands/schemas 轉成 PackageInfo，併進 ctx.packages
  │     → build_function_tools 照舊 → agent 看得到工具、prompt 也對
  └─ 這個 turn 第一次 exec（sandbox 懶惰建立）
        create(spec)  spec.tools = { name: sha }      ← 剛才 resolve 釘住的那組
        host 為這個 sandbox 組 tools 視圖 → 掛成 /.tools
```

**釘住是重點**：app 看到的 schema 與 sandbox 裡跑的 bundle，來自同一次 resolve 的同一個 sha。

### 4.2 磁碟 layout

```
/opt/tools/
  builtin/<name>/        第一方，烤在 image 裡（今天的內容原封搬進來）
  ext/<sha256>/          第三方，content-addressed

<sandbox-root>/.tools-view/        per-sandbox 視圖（root 擁有，infra area）
  sci-plot       → /opt/tools/builtin/sci-plot
  wafer-history  → /opt/tools/ext/<sha>
```

- **unjailed**：`.tools` 就是（或 symlink 到）這個視圖目錄；絕對路徑的 symlink 解得開。
- **jailed**：視圖裡的 symlink **在 jail 內會斷**——jail 以 `$ROOT` 為根，裡面沒有 `/opt`
  （bootstrap 自己就用 `/.tools/python-stack/launch` 這種 jail 內絕對路徑，
  `local_process.py:93`）。所以 jailed 走 **per-tool ro bind-mount**：
  `mount --bind /opt/tools/ext/<sha> $ROOT/.tools/<name>` + `remount,bind,ro`，一支一個 mount。

**副作用（正面）**：今天 jailed 是把**整個** tools 目錄掛進去，這個 app 沒授權的工具也看得到；
改成逐支掛之後，sandbox 只看得到這次授權的那幾支——**比今天更嚴**。

### 4.3 `app.json` 宣告（Q11 = a）

```json
"agent": {
  "tools": ["csv-column-summary", "sci-plot", "wafer-history", "wafer-history:trend"],
  "external_tools": {
    "wafer-history": "https://gitlab.example/api/v4/projects/123/jobs/artifacts/main/raw/dist/tool.manifest.json?job=build-tool"
  }
}
```

`tools` 完全照舊（授權、colon 語法挑 command 都不變 —— Q12）；`external_tools` 只回答
「這個名字的 bytes 去哪拿」。**換版本不用改 repo**（url 不變，作者推就生效）；只有**新增/移除
一支工具**要改 repo + 發版。

### 4.4 運維：怎麼回滾

url 指的是「latest」，所以平時會跟著作者走。要**釘回舊版**時，把 `external_tools` 那串換成
指定 job 的 artifact URL（GitLab 支援 `/jobs/<job_id>/artifacts/…`），下一個 turn 的 resolve
就會抓到那個 sha。若該 sha 還在 host cache 裡是**秒回**（content-address，P10 的 GC 沒收走就還在）；
被回收了就重抓一次，同一條路。

換句話說：**「跟著最新」和「釘死某版」是同一個欄位的兩種寫法**，不需要第二套機制。

---

## 5. 明確不做

- **不做 runtime registry / admin UI**（Q11 選 a）。
- **不在平台上 build 陌生碼**。
- **不把 sha 釘在 `app.json`**——釘了就等於作者每次發版都要改我們 repo，自動更新就沒了。
  （要釘特定版本時改的是 **url**，見 4.4。）
- **不讓 app 直連 GitLab**：憑證只在 host 一處。
- **不開放 user 自建 tool**：仍是 deploy-time 的動作，只是「dev」現在可以是外部作者。
- **第一方工具維持烤在 image 裡**：它跟平台同版本發布是對的，不硬要統一。

---

## 6. 已知風險（誠實列，非阻擋）

| | 風險 | 處置 |
|---|---|---|
| R1 | **sha 驗證退化成完整性檢查**。manifest 與 bundle 同源，能推 artifact 的人可以同時改掉 sha。擋得住傳輸截斷／cache 壞掉／只改一個檔的粗糙竄改；擋不住有 push 權限的人 | **信任邊界 = 誰能推那個 GitLab project**。該 project 的權限要當**部署權限**管。已知並接受 |
| R2 | GitLab 進了開 sandbox 的關鍵路徑 | host-local cache + **last-known-good**：抓不到就用上次成功那份並標 `stale`，不讓 sandbox 開不起來 |
| R3 | host 新增對外憑證面（今天 `sandbox-host/src` 完全沒有任何 httpx/token，只有測試用） | 只此一處，先一個全域 token（Q13） |
| R4 | 作者改 command 名／schema 會即時影響所有新 sandbox，沒有版本閘 | 以 P8 的「記錄每次用的 sha」+ 相容性閘門的清楚錯誤緩解；真要凍版就用 4.4 釘 job URL |
| R5 | **GitLab CI artifact 預設會過期**（`expire_in` 不設約 30 天）。過期後 URL 404 —— 已 cache 的 host 靠 last-known-good 撐著，但**新 pod 一起來就抓不到** | CI 範本強制 `expire_in: never`，並在 `tool-authoring.md` 講明；resolve 失敗訊息要能一眼看出「artifact 過期了」 |
| R6 | 磁碟：每支 bundle 約 150MB，× N 支 × 每台 host（而且新舊 sha 會並存） | P10 的 GC 加 `SANDBOX_HOST_TOOL_CACHE_MAX_BYTES` 上限；**沒被引用的 bundle 照留**（回滾才會是重掛而不是重抓），超過上限才由舊到新淘汰；沒設上限則不留 |
| R7 | 舊 sha 的 cache 會堆積 | P10 的 refcount GC |
| R8 | **作者的 breaking change 沒有事前通知管道**。改了 command 名或必填參數，我們是「使用者踩到」才知道 | 事前只有 `verify`（人工，且只在新增時跑）；事後靠 P8 記錄的 `name → sha + version` 查得回去。**接受**，並在 `tool-authoring.md` 把「這是 breaking」講白（§3.6） |

---

## 7. Phases

> **逐條核對**：`python3 scripts/check_third_party_tools_674.py` —— 60 條可執行檢查，全過。
> 那份腳本自己也做過變異測試（故意破壞受檢的性質，確認它會變紅）。

> 一個 phase 一個 commit，flat integer。每個 phase 都要有**會紅的新測試**。
> **P1–P3 先做完，作者就能開始寫工具了**（即使平台端還沒接完）。

### P1 · artifact 格式 + 驗證器（純函數，無 I/O）

**✅ 完成**（`tooling/artifact.py`，16 測試、100%）。純 stdlib，所以 builder image 匯入得動；host 有逐位元組相同的複本，由測試釘住。

`tooling/artifact.py`：`Manifest` struct、`parse_manifest(bytes)`、
`check_compatible(manifest, host_builder, arch)`、`verify_bundle(bytes, expected_sha)`。
builder 與 host 共用這一份契約。
測試涵蓋：欄位缺漏、`format_version` 不認得、builder 對不上、arch 不符、sha 不符、name 不符。

### P2 · builder image + `build-tool` / `smoke`

**✅ 完成**（`tooling/builder.py` + `tool-builder/`，20 測試、100%）。smoke 在 build 之內，失敗**刪掉整個輸出目錄**；一條測試釘住 builder base 必須等於 sandbox runtime base。

`tool-builder/Dockerfile`，base 對齊 `sandbox-host/Dockerfile` **stage 2**；
`build-tool <src> <out>` = 既有 `prebuild.build_package` + 抽 `commands.json`/`schemas/` 進 manifest
+ 打包 `tool.tar.gz`；`smoke <dist>` 解開跑一遍 3-stage 契約。
`BUILDER_ID` 同時烤進 builder 與 host image，供 P6 的閘門比對。
`version` 取自作者的 `pyproject`（Q14）。
**smoke 是 build 的最後一步，不過就不產出 artifact**（Q15）——這是唯一擋得住「作者沒測過就發版」
的地方，所以它必須在 build 之內，不能是另一個可略過的 job。
驗收：拿 `sample-tools/csv-column-summary` 走一遍，產出的 manifest 通過 P1 的驗證器；
故意弄壞 CLI 契約時 build 要失敗、且**不留下 artifact**。

### P3 · 作者面文件 + CI 範本

**✅ 完成**（`docs/tool-authoring.md` + `tool-builder/gitlab-ci.example.yml`）。`expire_in: never` 與「會被截斷／有時間上限」都有測試防止被拿掉。

`docs/tool-authoring.md`（§3 全文）+ `.gitlab-ci.yml` 範本（含 `expire_in: never`）。
**做完這裡，工具作者就可以開始寫、開始發版了**，不必等平台端。

### P4 · content-addressed cache + 安全解壓（host 本機，先不連網）

**✅ 完成**（`sandbox_host/tool_cache.py`，100%）。sha 格式閘門、`filter="data"`、先 staging 再 rename；兩道守衛都做過**變異測試**。

`sandbox_host/tool_cache.py`：`ensure(sha, tar_bytes) -> Path`。
解到 tmp → **atomic rename**；擋 **zip-slip**（`../` 路徑穿越）、symlink 逃逸、hardlink；
完成後 `chown root:root` + `chmod 755`（特權動作 seam 化，非 root 也能單元測）。
已存在同 sha 就 no-op。

### P5 · 第一方工具搬進 `builtin/`

**✅ 完成**。8 個既有測試按預期紅→綠；image 的 COPY 目標與程式的 `builtin/` 解析由測試綁在一起。

tools 目錄形狀從「一堆 `<name>/`」變成 `builtin/<name>/` + `ext/<sha>/`。
**這是 breaking change**（`sandbox-host/Dockerfile`、`SANDBOX_HOST_TOOLS_DIR`、
`discover_packages` 的路徑、既有測試都會動），所以獨立一個 phase，先把第一方搬完並全綠，
再讓第三方加進來。

### P6 · host fetch + `POST /tools/resolve`

**✅ 完成**（`tool_resolve.py` + `POST /tools/resolve`，100%）。**部分成功**回應、last-known-good、404 直接說「artifact 過期」。

抓 manifest（httpx，token 由 env 來）→ P1 的閘門 → cache 命中就跳過下載 → 否則抓 tar →
驗 sha → `tool_cache.ensure` → 回 `{name: {sha, commands, schemas, stale?}}`。
本機留一份 `url → 最後成功 sha` 的小索引以支撐 last-known-good。
錯誤訊息要能分辨「artifact 過期／404」「ABI 不合」「sha 不符」（R5）。
更新 `docs/sandbox-host-wire.md` 的端點表。

### P7 · per-sandbox tools 視圖（unjailed symlink ／ jailed per-tool ro bind-mount）

**✅ 完成**。per-sandbox 視圖；jailed 逐支 ro bind + tmpfs 封死（jail 內實跑驗證）。副作用：sandbox 只看得到被授權的工具，比改動前更嚴。

`SandboxSpec` 加 `tools: dict[str, str]`（本地名 → sha）。`create` 依此組 `.tools-view`；
jailed bootstrap 改成逐支 bind-mount（只掛這次授權的）。

### P8 · app 端：`external_tools` 宣告 + turn 起點 resolve + 記錄 sha/version

**✅ 完成**。`agent.external_tools` → turn 起點 resolve → sha 釘住整個 turn → `PackageInfo` 進 `ctx.packages`；拿不到的工具在 prompt 裡說明原因（不送去 tool picker，因為那裡沒有開關可按）。

`app.json` schema 加 `agent.external_tools`；`turn_context` 在組 context 時打 host resolve，
把回來的 commands/schemas 轉成 `PackageInfo` 併進 `ctx.packages`
（該欄位已存在：`agent/context.py:177`，由 `api/turn_context.py:355` 餵）；
把 `{name: sha}` 一起帶進 `ensure_sandbox` 的 spec。**同一 turn 內 sha 釘住。**
同時落一筆記錄（item / turn / name → **sha + version** / stale）——這是「使用者說工具怪怪的」
唯一查得回去的線索，也是 R8 的事後補償。
resolve 失敗且無 last-known-good → **該工具缺席但 turn 繼續**，並讓 agent 與使用者都看得到原因
（沿用 #480 的「停用工具也要揭露」形狀）。

### P9 · `verify` 指令（上架前驗收）+ 開機 best-effort 預熱

**✅ 完成**（`tooling/verify.py`，100%）。**不執行 bundle**（Q16b，plan 已改）；開機預熱**延後 readiness 但永不失敗**。

`python -m workspace_app.tooling verify <manifest-url>`：跑 P1 的閘門 → 抓 bundle → 驗 sha →
**在一個丟棄式 sandbox 裡跑一遍 smoke** → 回報能不能上、哪裡不合（Q16）。
人工指令，不是自動閘——目的是「貼進 `app.json` 再發版」之前就知道會不會爛。

同一個 phase 加開機**預熱**：啟動時對 `app.json` 裡的 `external_tools` 做 best-effort resolve
把 cache 灌熱，但**不 gate readiness**（Q17）——GitLab 掛掉不能讓 app 起不來。
這跟第一方 `discover_packages` 的 fail-loud（`__main__.py:149`）是刻意不同的取捨，
要在 code 註解裡寫清楚為什麼，否則下一個人會「順手統一」。

### P10 · cache GC + 容量上限

**✅ 完成**。沒被引用的 bundle **留著當回滾快取**，超過上限才由舊到新淘汰；正在使用的永遠不動。

refcount sweep：沒有任何 live sandbox 引用的 `ext/<sha>` 才回收（與 idle-killer / blob-gc 同形狀），
加一個 cache 上限（R6）。content-address ⇒ 回滾到還在 cache 的舊 sha 是秒回。

### P11 · 改寫 `extending-the-platform.md` 的立場 + 運維文件

**✅ 完成**。`extending-the-platform.md` 的「只有 dev 自建」立場已改寫成兩條路；`deployment.md` 補上 token／磁碟／**怎麼退回**。

該頁 §Tool 目前寫「只有 dev 自建」、流程是「改 `PACKAGES` → prebuild → 重啟」，
要變成兩條路：第一方（vendor 進 repo）與第三方（作者 CI + artifact url）。
`deployment.md` 補：全域 token 怎麼給、磁碟估算、**怎麼回滾**（§4.4）、
以及 §3.7 那張「誰做什麼」表。

### P12 · 權限不變量 + 端到端整合測試

**✅ 完成**。端到端測試走完整條鏈並驗證版本隔離；`.tools-view` 不被 uid 擁有的不變量做過變異測試。root-gated 那條在這台機器**只寫了、沒跑過**（非 root）。

root-gated integration：解壓後 `owner=0` / other 無 `w`；降權 uid 寫不進；
jail 內只看得到這次授權的工具；換 sha 後**下一個** sandbox 拿到新版而**既有** sandbox 不變。
另加一條單元測試釘住「`.tools-view` 目錄是 root 擁有、`_own`/`reown` 不碰 infra siblings」
——這是新形狀下新增的承重點（`sandbox-host/src/sandbox_host/isolated_process.py:228`
的註解是今天的依據）。

---

### P13 · dev 相依不再被打包

**✅ 完成**（`tooling/prebuild.py`）。

`uv sync` 預設會裝 `dev` 群組，所以把 pytest 正確放在 `[dependency-groups] dev` 的作者，
**還是會被打包進去**——我們自己的 `sample-tools/csv-column-summary` 就帶著 pytest 9.0.3。
作者從自己的檔案看不出這件事，因為他的檔案是對的。

修法是 `uv sync` 補 `--no-dev`。測試分兩層：unit 釘 argv，integration **真的 build 一顆
bundle 再進去翻**——受測的行為是 uv 的，我們傳的旗標若 uv 不再認得就毫無意義。

### P14 · 體積上限與例外憑證

**✅ 完成**（`tooling/grant.py`、`tooling/builder.py`、`tooling/verify.py`、`tool-builder/Dockerfile`）。

- **規則只有一份**：`grant.check_size`，作者的 build 與我們的 `verify` 都呼叫它。
- **作者端**：`build-tool` 量壓縮後的 tar.gz，超標時列出 bundle 裡佔比 ≥1% 的最重項目，
  作者不必猜要砍哪個。那次檢查跑在作者自己的 runner 上，作用是早點發現；閘門是我們這道。
- **平台端**：`verify` **在抓 bundle 之前**就用 manifest 的 `bundle.size` 判斷——體積寫在
  manifest 裡就是為了不必下載一 GB 才知道它是一 GB。
- **憑證**：ed25519，一行可貼進信件；綁 tool id（憑證是公開的，不綁就等於誰都能用）。
  `TRUSTED_KEYS` 是列表，可輪替。`keygen` 用 `O_EXCL` + `0600` 建私鑰，拒絕覆寫。
- **真的建一次才發現的設計錯誤**：41MB 的 bundle 帶著一張平台看不懂的憑證，整個 build 失敗——
  理由和它的體積無關。改成**憑證只在超過預設上限時才被查閱**。
- builder image 加了 `cryptography`（build path 唯一的第三方 import）。有測試從 import graph
  推出這份清單並要求 Dockerfile 裝的**恰好等於它**，雙向：少裝會壞在別人的 pipeline，
  多裝是每個作者 CI 的負擔。

## 8. 這輪之外

- **多租戶／跨 app 的工具共享**：現在 scope 在 app.json，夠用。
- **非 GitLab 的來源**（S3 / OCI artifact / GitLab package registry）：manifest 與 cache 都是
  URL-agnostic，之後要加只動 P6。若 R5 的 `expire_in` 在實務上治不住，package registry 是下一步。
- **per-project token**（Q13 先做全域）。
- **工具內部呼叫 LLM**：另一條線（2026-07-24 同場討論），與本案無耦合。
