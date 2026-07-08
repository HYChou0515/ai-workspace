# Plan: 區分 sandbox durable filestore 與 API specstar filestore (#501)

> **#501**:`nfs_tree`(#492 的 workspace 持久層)目前掛在全域 `filestore.kind`。
> 這讓「開 sandbox 的 NFS 持久」等於「把整個 app 的 filestore 換成 NFS 樹」——
> 語意錯誤,而且 pure `nfs_tree` 模式下 `SpecstarFileStore` 根本不會被建構,
> **API 的 specstar filestore(WorkspaceFile 註冊 / blob GC / #219 遷移 / 與 KB·wiki
> 共用 blob pool)整個消失**。#492 的原意是 `nfs_tree` **只針對 sandbox** 的持久。

## 現況查證(為什麼要修)

`get_filestore(settings.filestore.kind)` 產出的那**一個** filestore,只被
workspace/item-scoped 的東西用(`SandboxSync`、file 工具 facade、item routes、
`entity/*.ai.yaml`、hub collections、context files、seeding、workspace blob GC)。
KB(`SourceDoc.content` = specstar `Binary`,`Ingestor(spec)`)與 wiki
(`WikiFileStore(spec)`)**都直接吃 `spec`,不經過這個 filestore**。

所以行為上 `kind: nfs_tree` 只搬 workspace 檔,但 **config 把「sandbox 持久機制」
綁在「API 的 filestore 總開關」上是錯的**:選 `nfs_tree` 就等於宣告整個 app 的
specstar filestore 不見了。

## 目標

**把兩個角色在 config + 建構層明確區分**,預設零行為變化:

- `filestore.kind`(`memory | specstar`)= **API 的 specstar filestore**,永遠保留,
  負責 `WorkspaceFile` 註冊 / blob GC / #219 / 與 KB·wiki 共用 blob pool。
- **sandbox 的持久層**(可選 `nfs_tree`)搬到 `sandbox.durable.*`。預設 `kind: ""`
  = 直接沿用 API 的 specstar filestore(現有部署完全不變)。
- `nfs_tree` 的 M2 fallback **重用同一份 API specstar filestore 實例**,不再另建
  第二個 `SpecstarFileStore`(現在的 `_build_nfs_tree_filestore` 會)。

即:pure-nfs 模式下,API specstar filestore **仍被建構**(WorkspaceFile 仍註冊,
blob GC 仍完整),workspace 檔則落在 NFS 樹——兩者井水不犯河水。

## Phases(flat integer、TDD)

- **P1 · config schema + loader**
  - `schema.py`:新增 `SandboxDurableSettings(kind="", nfs_root="", migrate_from="")`;
    `SandboxSettings` 加 `durable` 欄位。`FilestoreSettings.kind` 註解收斂成
    `memory | specstar`;**移除** `FilestoreSettings.nfs_root` / `migrate_from`(搬到 durable)。
  - `loader.py`:`_TOP_SCHEMA["sandbox"]` 加 `"durable"`;`_build_sandbox` 建 `durable`
    (比照現有 `http` / `isolation` 巢狀處理)。
  - Unit:loader 解析 `sandbox.durable.*` + 預設;`FilestoreSettings` 不再有 nfs 欄位。

- **P2 · factories 拆分**
  - `get_filestore(settings, spec)` → API specstar filestore,只收 `memory | specstar`
    (拿掉 `nfs_tree` case)。
  - 新 `get_sandbox_filestore(settings, spec, api_filestore)` → sandbox 持久:
    `durable.kind == ""` ⇒ 回 `api_filestore`;`"nfs_tree"` ⇒ `NfsTreeFileStore(nfs_root)`
    (缺 `nfs_root` 直接報錯),`migrate_from == "specstar"` ⇒
    `MigratingFileStore(nfs, api_filestore)`(**重用實例**)。
  - 移除 `_build_nfs_tree_filestore`。
  - Unit:`get_filestore` 拒 `nfs_tree`;`get_sandbox_filestore` 三種 case(預設重用、
    pure-nfs、nfs+migrate 重用同一 api 實例)。

- **P3 · 接線(`__main__`;`create_app` 單一 param 不動)**
  - `__main__`:先 `api_filestore = get_filestore(...)`(建構即註冊 WorkspaceFile),
    再 `sandbox_filestore = get_sandbox_filestore(..., api_filestore)`;
    `create_app(filestore=sandbox_filestore, ...)`。#219 遷移 gate 維持
    `settings.filestore.kind == "specstar"`(=API filestore)。
  - `create_app` 不改簽章:它的 `filestore` param 一律是 workspace/sandbox 持久層;
    blob GC 走 `spec`,只要 `api_filestore` 在 boot 時建構過即完整。
  - Unit:standalone boot 兩個都建;`nfs_tree` durable 下 WorkspaceFile 仍註冊。

- **P4 · docs + 範例 config + 既有測試遷移**
  - `config.example.yaml`:`sandbox.http` 註解的 `filestore.kind: nfs_tree` 改指
    `sandbox.durable.kind: nfs_tree`;在 `sandbox:` 下補 `durable:` 範例區塊。
  - `docs/plan-issue-492.md` + `docs/configuration.md`:更新 nfs_tree 現在的位置。
  - 把用到 `filestore.kind: nfs_tree` 的既有測試(`tests/test_factories.py`、
    `tests/filestore/test_migrating.py` 等)改成新 API / `sandbox.durable`。

## 非目標 / 相容性

- `create_app` 的 `filestore` param 語意不變(=workspace/sandbox 持久),測試沿用。
- KB / wiki 完全不動(本來就吃 `spec`)。
- 預設(`sandbox.durable.kind: ""`)= sandbox 持久沿用 API filestore = **現有部署零變化**。
