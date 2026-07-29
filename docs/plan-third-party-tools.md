# 第三方 tool 散布：作者跑自己的 CI，新 sandbox 自動帶上

> Issue: [#674](https://github.com/HYChou0515/ai-workspace/issues/674)。狀態：**plan，未動工**。

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
| Q3 | artifact 長什麼樣？ | 兩個檔：`tool.tar.zst`（bundle）+ `tool.manifest.json`。放 GitLab CI artifact |
| Q4 | manifest 欄位 | `format_version` / `name` / `commands`（含 schema）/ `builder`（ABI 錨）/ `python`+`arch` / `bundle.sha256` / `source`（git+sha，純溯源） |
| Q5 | 「能跑的目錄」放哪？ | **host 本機磁碟**。共享 NFS 放 `/opt/tools` 已被否決——NFS 不能設權限，`root:root 755` 守不住 |
| Q6 | 註冊表放哪一層？ | **app.json**。不做 runtime registry、不做 admin UI |
| Q7 | 什麼時候查 GitLab？ | **開 sandbox 時**（實作上再往前一格到 turn 起點，見 Q9） |
| Q8 | `/opt/tools` 的 layout | **content-addressed**（`ext/<sha>/`）。`.tools` 那層把 sha **還原成 tool name**，工具端零改動 |
| Q9 | app 怎麼拿到 schema？ | **問 host**（新的 resolve 端點）。GitLab 憑證只在 host 一處 |
| Q10 | 工具的名字誰說了算？ | **我們**：`app.json` 的 key 是本地名；manifest 的 `name` 只當校驗 |

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

## 3. 目標形狀

### 3.1 一次 turn 的資料流

```
turn 開始
  ├─ app 讀 app.json:  tools: [...第一方...],  external_tools: { 本地名 → artifact url }
  ├─ app → host   POST /tools/resolve  { tools: { name: url } }
  │     host 對每支：抓 manifest → 相容性閘門(format_version / builder / arch)
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

### 3.2 磁碟 layout

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

### 3.3 `app.json` 宣告

```json
"agent": {
  "tools": ["csv-column-summary", "sci-plot", "wafer-history"],
  "external_tools": {
    "wafer-history": "https://gitlab.example/api/v4/projects/123/jobs/artifacts/main/raw/dist?job=build"
  }
}
```

`tools` 完全照舊（授權、colon 語法挑 command 都不變）；`external_tools` 只回答「這個名字的
bytes 去哪拿」。**換版本不用改 repo**（url 不變，作者推就生效）；只有**新增/移除一支工具**
要改 repo——那本來就是部署層的決定。

---

## 4. 明確不做

- **不做 runtime registry / admin UI**（那是被否決的另一案）。
- **不在平台上 build 陌生碼**。
- **不把 sha 釘在 `app.json`**——釘了就等於作者每次發版都要改我們 repo，自動更新就沒了。
- **不讓 app 直連 GitLab**：憑證只在 host 一處。
- **不開放 user 自建 tool**：仍是 deploy-time 的動作，只是「dev」現在可以是外部作者。
- **第一方工具維持烤在 image 裡**：它跟平台同版本發布是對的，不硬要統一。

---

## 5. 已知風險（誠實列，非阻擋）

| | 風險 | 處置 |
|---|---|---|
| R1 | **sha 驗證退化成完整性檢查**。manifest 與 bundle 同源，能推 artifact 的人可以同時改掉 sha。擋得住傳輸截斷／cache 壞掉／只改一個檔的粗糙竄改；擋不住有 push 權限的人 | **信任邊界 = 誰能推那個 GitLab project**。該 project 的權限要當**部署權限**管。已知並接受 |
| R2 | GitLab 進了開 sandbox 的關鍵路徑 | host-local cache + **last-known-good**：抓不到就用上次成功那份並標 `stale`，不讓 sandbox 開不起來 |
| R3 | host 新增對外憑證面（今天 `sandbox-host/src` 完全沒有任何 httpx/token，只有測試用） | 只此一處；token 走既有的 secret 機制 |
| R4 | 作者改 command 名／schema 會即時影響所有新 sandbox，沒有版本閘 | 以 P6 的「記錄每次用的 sha」+ 相容性閘門的清楚錯誤緩解 |
| R5 | 舊 sha 的 cache 會堆積 | P7 的 refcount GC |

---

## 6. Phases

> 一個 phase 一個 commit，flat integer。每個 phase 都要有**會紅的新測試**。

### P1 · artifact 格式 + 驗證器（純函數，無 I/O）

`tooling/artifact.py`：`Manifest` struct、`parse_manifest(bytes)`、
`check_compatible(manifest, host_builder, arch)`、`verify_bundle(bytes, expected_sha)`。
測試涵蓋：欄位缺漏、`format_version` 不認得、builder 對不上、arch 不符、sha 不符。

### P2 · content-addressed cache + 安全解壓（host 本機，先不連網）

`sandbox_host/tool_cache.py`：`ensure(sha, tar_bytes) -> Path`。
解到 tmp → **atomic rename**；擋 **zip-slip**（`../` 路徑穿越）、symlink 逃逸、hardlink；
完成後 `chown root:root` + `chmod 755`（特權動作 seam 化，非 root 也能單元測）。
已存在同 sha 就 no-op。

### P3 · host fetch + `POST /tools/resolve`

抓 manifest（httpx，token 由 env 來）→ P1 的閘門 → cache 命中就跳過下載 → 否則抓 tar →
驗 sha → `tool_cache.ensure` → 回 `{name: {sha, commands, schemas, stale?}}`。
本機留一份 `url → 最後成功 sha` 的小索引以支撐 last-known-good。
更新 `docs/sandbox-host-wire.md` 的端點表。

### P4 · per-sandbox tools 視圖（unjailed symlink ／ jailed per-tool ro bind-mount）

`SandboxSpec` 加 `tools: dict[str, str]`（本地名 → sha）。`create` 依此組 `.tools-view`；
jailed bootstrap 改成逐支 bind-mount（只掛這次授權的）。第一方繼續從 `builtin/` 來。

### P5 · app 端：`external_tools` 宣告 + turn 起點 resolve

`app.json` schema 加 `agent.external_tools`；`turn_context` 在組 context 時打 host resolve，
把回來的 commands/schemas 轉成 `PackageInfo` 併進 `ctx.packages`
（該欄位已存在：`agent/context.py:177`，由 `api/turn_context.py:355` 餵）；
把 `{name: sha}` 一起帶進 `ensure_sandbox` 的 spec。**同一 turn 內 sha 釘住。**
resolve 失敗且無 last-known-good → **該工具缺席但 turn 繼續**，並讓 agent 與使用者都看得到原因
（沿用 #480 的「停用工具也要揭露」形狀）。

### P6 · 記錄每次實際用到的 sha

每次 resolve 落一筆（item / turn / name → sha / stale）。這是「使用者說工具怪怪的」
唯一查得回去的線索，也是回滾時「要回到哪一版」的依據。

### P7 · cache GC

refcount sweep：沒有任何 live sandbox 引用的 `ext/<sha>` 才回收（與 idle-killer / blob-gc 同形狀）。
content-address ⇒ 回滾到還在 cache 的舊 sha 是秒回，被 GC 了就重抓，同一條路。

### P8 · builder image + 作者端 CI 範本

`tool-builder/Dockerfile`（從 host runtime base 長出來）+ `.gitlab-ci.yml` 範本；
`BUILDER_ID` 烤進 host image，供 P3 的閘門比對。
作者的 CI 只要：`docker run --rm -v $PWD:/src <builder>` → `dist/tool.tar.zst` + `dist/tool.manifest.json`。

### P9 · docs（作者面 + 改寫現有立場）

新增 `docs/tool-authoring.md`：3-stage CLI 契約、builder image 用法、CI 範本、manifest 欄位、
ABI 規則、可用的環境變數（接 #669）。
改寫 `docs/extending-the-platform.md` §Tool——它目前寫「只有 dev 自建」、
流程是「改 `PACKAGES` → prebuild → 重啟」，要變成兩條路：第一方（vendor 進 repo）
與第三方（作者 CI + artifact url）。

### P10 · 權限不變量 + 端到端整合測試

root-gated integration：解壓後 `owner=0` / other 無 `w`；降權 uid 寫不進；
jail 內只看得到這次授權的工具；換 sha 後**下一個** sandbox 拿到新版而**既有** sandbox 不變。
另加一條單元測試釘住「`.tools-view` 目錄是 root 擁有、`_own`/`reown` 不碰 infra siblings」
——這是新形狀下新增的承重點（`sandbox-host/src/sandbox_host/isolated_process.py:228`
的註解是今天的依據）。

---

## 7. 這輪之外

- **多租戶／跨 app 的工具共享**：現在 scope 在 app.json，夠用。
- **非 GitLab 的來源**（S3 / OCI artifact）：manifest 與 cache 都是 URL-agnostic，之後要加只動 P3。
- **工具內部呼叫 LLM**：另一條線（2026-07-24 同場討論），與本案無耦合。
