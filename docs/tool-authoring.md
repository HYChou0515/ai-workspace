# 寫一支工具給這個平台（給外部工具作者）

你不需要我們 repo 的權限，也不用等我們發版。你在**自己的 GitLab** 寫工具、跑一個 CI job，
把產出的 artifact 網址給我們一次；之後你每推一版，**下一個開起來的 sandbox 就自動用新版**。

這頁只講你要做的事。平台那邊怎麼抓、怎麼掛，你不用知道。

---

## 1. 你的 repo 長什麼樣

```
my-wafer-tools/
  pyproject.toml          # 一個 [project.scripts] 進入點 + version
  uv.lock                 # 依賴釘死 —— 沒有它就沒有可重現的 build
  src/wafer_tools/
    cli.py                # 三段式契約（見下）
    commands/…
  .gitlab-ci.yml          # 我們提供的範本，貼上就好
```

`pyproject.toml` 只有兩件事是硬性的：

```toml
[project]
name = "wafer-tools"
version = "1.4.2"          # 必填。這是人在對話裡講的版本號

[project.scripts]
wafer-history = "wafer_tools.cli:main"   # 必須「剛好一個」
```

**為什麼只能一個 script**：bundle 的啟動器就是去執行這個進入點。兩個會讓「AI 剛才到底跑了哪一個」
變得沒有答案，零個則沒東西可跑。一個 package 可以有**很多 command**——那是下一節的事，不是靠多個
script 達成的。

## 2. 三段式契約

你的 CLI 要能回答三種呼叫。前兩種是**給機器看的自我描述**，第三種才是幹活：

| 呼叫 | 要印出 |
|---|---|
| `wafer-history` | `[{"name": ..., "description": ...}, …]` —— 我有哪些 command |
| `wafer-history trend` | `{"name":…, "description":…, "params_json_schema":…}` —— 這個 command 吃什麼參數 |
| `wafer-history trend '<json>'` | 真的執行，結果印到 stdout |

**你不用手寫 JSON schema。** 用 pydantic 定義參數，`Args.model_json_schema()` 就是那個 schema；
build 的時候我們會跑你的 CLI 把它抓下來凍進 artifact。

（完整範例與 decorator 版的寫法，見
[擴充平台的 Tool 一節](extending-the-platform.md)——第一方工具用的是同一套契約。）

## 3. `.gitlab-ci.yml`

複製 [`tool-builder/gitlab-ci.example.yml`](https://github.com/HYChou0515/ai-workspace/blob/master/tool-builder/gitlab-ci.example.yml)：

```yaml
build-tool:
  image: registry.example/ai-workspace/tool-builder:2026.07
  script:
    - build-tool /src dist
  artifacts:
    paths: [dist/]
    expire_in: never          # 見下面的警告
```

產出兩個檔：

```
dist/tool.tar.gz         # 會跑的整包（你的程式 + 依賴 + 一顆 python）
dist/tool.manifest.json  # 平台讀的描述檔
```

!!! warning "`expire_in: never` 不是可選的"

    GitLab 的 CI artifact **預設會過期**（通常 30 天）。過期之後那個網址就 404，
    而已經抓過的機器還能靠快取撐著，**新開的機器會拿不到你的工具**。
    這種壞法最難查，因為「昨天還好好的」。

!!! danger "一定要在 builder image 裡 build"

    bundle 帶著自己的 python 和原生套件（numpy 那類），**只能在它被 build 出來的那個底層上跑**。
    在別的 image build 出來的東西，平台會**當場拒絕**——這比讓它在執行時 segfault 好，
    因為那種錯會發生在使用者面前，而且看不出跟你的 build 有關。

## 4. 不推 CI 也能先自己測

```sh
docker run --rm -v "$PWD:/src" -v "$PWD/dist:/dist" \
    registry.example/ai-workspace/tool-builder:2026.07 build-tool /src /dist

docker run --rm -v "$PWD/dist:/dist" \
    registry.example/ai-workspace/tool-builder:2026.07 smoke /dist
```

`smoke` 會把 bundle 解開、**在平台真正執行工具的那個底層裡**跑一遍三段式契約。
所以「我這邊會動」跟「平台上會動」是同一件事，不是碰運氣。

## 5. 交給我們的，就一串網址

```
https://gitlab.example/api/v4/projects/<id>/jobs/artifacts/<ref>/raw/dist/tool.manifest.json?job=build-tool
```

這是 GitLab 的「最新 artifact」端點——**你推一版，它就指到新的**，我們不用改任何東西。

---

## 6. 平台**會擋**什麼

這些不是建議，是有程式在擋的：

| | 會怎樣 |
|---|---|
| 三段式契約不完整 | `build-tool` 直接失敗 |
| 沒有 `uv.lock` | build 失敗——沒有它 bundle 不可重現 |
| **smoke 沒過** | **build 失敗，而且不留下任何 artifact**（免得 CI 把壞的傳上去） |
| 不是在 builder image 裡 build 的 | 平台拒絕掛載 |
| `[project.scripts]` 不是剛好一個 / 沒有 `version` | build 失敗 |
| manifest 裡的名字跟我們登記的不一樣 | 平台拒絕——代表這個網址指到了別支工具 |

還有兩件**不是拒絕、但一定會發生**的事，不知道就會出事：

- **你的輸出會被截斷。** 每個工具的輸出都有上限（AI 的上下文是有限的）。要回大量資料，
  請寫成檔案再回一個路徑，不要整包往 stdout 倒。
- **執行有時間上限。** 預設整體 60 秒、閒置 60 秒（沒有輸出就算閒置）。長時間的工作要嘛
  切小，要嘛持續印進度。

## 7. 平台**不會擋**，但你該知道

- **description 要好好寫。** AI 會不會用你的工具、參數填不填得對，幾乎完全取決於
  command 和參數的 description——它們會被原封不動放進 AI 的提示裡。名字取得再好，
  description 寫「do stuff」就等於沒有。

- **改 command 名、或加一個必填參數，是 breaking change，而且立刻生效。**
  沒有版本閘：你推上去，下一個開起來的 sandbox 就是新的。要改請發一個新的 command 名，
  或先跟我們說一聲。

- **不要把 secret 放進 bundle。** 同一台機器上所有 sandbox 共用同一份 bundle。
  需要金鑰的話走環境變數（見
  [擴充平台的 Tool 一節](extending-the-platform.md)）。

- **`source` 欄位是給人看的溯源。** 平台不會回頭 clone 你的 repo，也不會因為
  「它來自 git」就更信任它。

---

## 8. manifest 裡有什麼

你不用手寫，`build-tool` 會產生。但看得懂它有助於查問題：

| 欄位 | 意思 |
|---|---|
| `format_version` | manifest 的格式版本。平台不認得就拒絕 |
| `name` / `version` | 你的工具叫什麼、哪一版（`version` 純粹給人看） |
| `commands` | 每個 command 的 name / description / JSON schema |
| `builder` | **你是在哪個 builder image 裡 build 的**。平台拿它比對 ABI |
| `python` / `arch` | 直譯器版本與 CPU 架構 |
| `bundle.sha256` / `bundle.size` | bundle 的指紋與大小 |
| `source` | git 網址與 commit，純溯源 |

## 9. 上架 / 更新 / 退回，誰做什麼

| 場合 | 你 | 我們 |
|---|---|---|
| **第一次上架** | 寫工具 → CI → 給網址 | 驗過 → 登記 → 發版 |
| **你發新版** | push 就好 | **什麼都不用做** |
| **要退回舊版** | （可選）自己回退 | 把登記的網址指到特定那次 build |
| **下架** | — | 從設定拿掉 |
