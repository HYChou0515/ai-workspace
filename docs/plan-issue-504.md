# Plan — #504 隔離 sandbox 的檔案 owner 不對

## 問題(RCA 摘要)

`IsolatedProcessSandbox` 的 exec 以 per-item uid `setpriv` 降權,但唯一設 owner 的地方是
`create` 時的 `_provision`:只 `os.chown(workspace, uid, -1)`(**非遞迴,只頂層**)+ 遞迴
default ACL `u:{uid}:rwx`。設計意圖是「app/host 之後寫進來的檔靠 default ACL 拿存取權」,
owner 刻意保持 root。三個症狀都是這個破口:

| 症狀 | 寫入路徑 | owner=root 原因 |
|---|---|---|
| NAS restore 不還原 uid | host `NfsArchive.restore` = `rsync -rlptD`(無 `-o`) | rsync 以 root 建檔;`_provision` chown 只頂層又在 restore 前 |
| user 上傳檔 uid 不對 | `LocalProcessSandbox.upload_file` → `shutil.copyfile` | 沒 chown |
| AI create_file 沒 uid | `LocalProcessSandbox.upload` → `write_bytes` | 沒 chown |

**為何 owner 要對、ACL 不夠**:
- git `detected dubious ownership`(硬檢查 `st_uid == geteuid()`,ACL 不算)。
- `chmod +x` 自己的檔 EPERM(只有 owner/root 能 chmod)。
- ACL masking:rsync `-p` chmod 成來源 mode,把 named-user ACL 壓到 group bits,0644 檔的
  `u:uid:rwx` 可能 mask 成 `r--` → 連存取權都退化成唯讀。

## 修法核心原則

**凡是經 app/host 進程寫進「隔離 live workspace」的檔/目錄(restore、upload、
upload_file、mkdir、rename),寫完都要 chown 成 per-item uid** —— 讓 owner 對,不只 ACL 對。
default ACL 保留當防呆(對任何殘留的 root 寫入路徑),但主力正確性機制改成真 ownership。
exec 內產生的檔本來就由降權 uid 建立、owner 已對,不受影響。

兩份幾乎相同的 `IsolatedProcessSandbox` 都要修:
- `src/workspace_app/sandbox/isolated_process.py`(local backend;uid 由 item id 衍生)
- `sandbox-host/src/sandbox_host/isolated_process.py`(http/production;uid pool 配發)

兩條 restore 路徑不同,決定修法落點:
- **local**:restore = `SandboxSync.restore` → 逐檔 `sb.upload_file`(`sync/sandbox_sync.py:106`)
  → 由「寫入時 chown」自動涵蓋,不需另外的遞迴步驟。
- **http**:restore = `_HostController.create` 內整批 `NfsArchive.restore`(不經 `upload_file`)
  → 需在 restore 後對整棵 workspace 遞迴 reown。

## 設計

### 1. base `LocalProcessSandbox` 加 `_own` hook(no-op)+ chown seam

在 base 加一個保護方法,寫入方法尾端呼叫;base 版 no-op(非隔離的單租戶 dev/host 自己擁有
所有寫入,無需 chown)。

```python
def _own(self, target: Path) -> None:
    """Hook: make `target` (and any parent dirs this write just created, up to
    the workspace root) owned by the sandbox principal. No-op in the base;
    IsolatedProcessSandbox chowns to the per-item uid so app/host-written files
    match the dropped exec uid — owner, not just ACL."""
    return None
```

在 `upload` / `upload_file` / `mkdir` / `rename` 建立目標後各呼叫一次 `self._own(target)`
(rename 也 chown 目的地;搬進來的檔會保留舊 owner)。

**chown seam**(比照現有 `AclRunner` seam,讓非 root 單元測試可覆蓋且可斷言目標 uid):

```python
ChownRunner = Callable[[Path, int], None]  # (path, uid) -> chown(path, uid, -1)
```

預設 `os.chown(path, uid, -1)`;測試注入 spy 記錄 `(path, uid)`,不需 root。

### 2. `IsolatedProcessSandbox._own` 實作(兩份)

chown `target` 本身,再往上走 parents,逐一 chown 直到(不含)workspace 根 —— 確保
workspace→target 整條新建目錄鏈都是 uid 所有(不然中途 root-owned 目錄會擋 git/rmdir 等
需要 owner 的操作;default ACL 只給存取權)。已是 uid 的 chown 是冪等 no-op,成本 O(depth)。

- app:uid = `self._uid_for(handle.id)`。但 `_own` 只有 `target`,需 workspace 邊界 →
  由 handle 反推,或改成寫入方法把 `(handle, target)` 傳進 `_own`。**採後者**:把 base 的
  `_own(target)` 改成 `_own(handle, target)`,base 仍 no-op;isolated 由 handle 取 uid +
  `_workspace(handle)` 邊界。
- host:uid = `self._identities[handle.id].uid`。

### 3. http restore 後遞迴 reown

app `LocalProcessSandbox`/host 端加 `reown(handle)`:遞迴把 workspace 整棵 chown 成該
handle 的 uid(host 由 `_identities` 取 uid)。base no-op。

`_HostController.create`(`sandbox-host/src/sandbox_host/app.py:210-213`)在
`archive.restore(...)` 之後、`mark_ready` 之前呼叫;duck-typed
(`getattr(self.sandbox, "reown", None)`)所以 `MockSandbox` 不受影響。`NfsArchive` 維持
ownership-agnostic(純 rsync 工具,不與隔離耦合)。

### 4. 權限 / capabilities

chown 到「別的」uid 需 CAP_CHOWN(或 root)。現有 `_provision` 已在 production chown 成功
→ 環境已具備;確認 `kubernetes/base/deployment.yaml`(app)與 sandbox-host 的
securityContext caps 有 CHOWN(多半已有,補確認即可)。非 root dev fallback → 隔離關閉 →
base no-op,不觸發 chown。

## Phases(flat)

- **P1 — app `IsolatedProcessSandbox` 寫入時 chown**
  base `_own(handle, target)` hook(no-op)+ `ChownRunner` seam;isolated 實作 chown
  target + 新建父鏈到 uid;`upload`/`upload_file`/`mkdir`/`rename` 尾端呼叫。TDD:spy 斷言
  每種寫入 chown 到衍生 uid;uid_range=1(uid==getuid)非 root 覆蓋。**同時修好 local
  的 upload、create_file、restore(逐檔 upload_file)**。

- **P2 — host `IsolatedProcessSandbox` 寫入時 chown**
  在 sandbox-host 那份複製相同 hook/seam/實作(uid 由 `_identities` 取)。TDD 對稱。

- **P3 — host restore 後遞迴 reown**
  host isolated 加 `reown(handle)`(遞迴 chown 到 uid);`_HostController.create` 在
  restore 後、mark_ready 前 duck-typed 呼叫。TDD:有 archive+item 時 reown 到 uid;無
  archive/item 時跳過;MockSandbox 無 `reown` 不炸。

- **P4 — caps + docs + gate**
  確認 k8s + sandbox-host securityContext 有 CAP_CHOWN;更新兩份 `isolated_process.py` 與
  `nfs_tree.py`/`nfs_archive.py` 的 docstring(ownership 為主、default ACL 為防呆);跑
  full local suite + `coverage report --fail-under=100` + `ruff` + `ty`(含 sandbox-host)。

- **P5 —(可選)root-gated integration**
  真正 foreign-uid ownership 端到端:restore 一檔後 exec `stat -c %u` == uid、`git init`
  在 restored repo 內不報 dubious ownership、user 上傳的 script `chmod +x` 成功。比照現有
  「真特權 enforcement 留 root-gated integration」慣例。

## 驗證 DoD

- 單元:三種寫入 + restore 後,目標 chown 到正確 per-item uid(spy 斷言),兩 backend。
- 整合(root-gated):restored/uploaded/created 檔在 sandbox 內 `stat` owner == 降權 uid;
  git dubious-ownership 消失;`chmod +x` 自己的檔成功。
- 100% gate(full local suite)綠;ruff/ty 綠。
