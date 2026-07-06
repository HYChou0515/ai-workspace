# 發布與更新紀錄（Releasing & Changelog）

平台的更新紀錄(release notes)同時出現在兩個地方——GitHub 的 Releases 頁,以及網頁的
**說明 → 更新紀錄**(`/help/releases`)——而且兩邊內容**同源**:都由
[`src/workspace_app/help_content/CHANGELOG.md`](https://github.com/HYChou0515/ai-workspace/blob/master/src/workspace_app/help_content/CHANGELOG.md)
衍生。這份 CHANGELOG 由 [git-cliff](https://git-cliff.org/) 從 **Conventional Commits**
自動生成,設定在
[`cliff.toml`](https://github.com/HYChou0515/ai-workspace/blob/master/cliff.toml)。(#441)

> 對照閱讀:[開發者指南](development.md)(commit 規範與品質 gate)。

---

## 1. 面向使用者的變更請用規範化 commit

只有規範化的 commit 才會進更新紀錄,並依前綴分類:

| commit 前綴 | 更新紀錄分類 | 網頁預設顯示 |
| --- | --- | --- |
| `feat:` | Added(新增) | ✅ 重點 |
| `fix:` | Fixed(修復) | ✅ 重點 |
| `perf:` | Performance(效能) | ✅ 重點 |
| `refactor:` | Changed(變更) | 只在「詳細」 |
| `docs:` | Documentation(文件) | 只在「詳細」 |

不符合格式的 commit(例如 `--`、`wip`)以及 `chore` / `ci` / `test` / `style` / 合併 commit
**會被忽略,不會出現在更新紀錄**。所以真正面向使用者的變更請務必用上面的前綴。scope 慣例是
issue 編號,例如 `fix(#465): …`;git-cliff 會把結尾的 `(#465)` 收整乾淨。

網頁的
[`/help/releases`](https://github.com/HYChou0515/ai-workspace/blob/master/web/src/pages/ReleasesPage.tsx)
預設只顯示「重點」(feat/fix/perf),可切到「詳細」看全部;`[Unreleased]` 區段永遠不會顯示。

```bash
make changelog-preview          # 預覽尚未釋出的紀錄(從 commit 生成,不寫檔)
```

## 2. 釋出(維護者操作)

版號採 **CalVer**:`vYYYY.MM.DD`,同一天再發一版時自動加後綴 `.1`、`.2`……
(例:`v2026.07.06` → `v2026.07.06.1`)。由維護者執行:

```bash
make release
```

它會:偵測今天的版號(含同日後綴)→ 把 `pyproject.toml` 的 `version` bump 成 PEP 440
正規化後的值(例 `2026.7.6`)→ 用 git-cliff 把尚未釋出的 commit 收成 `## [X]` 區段
prepend 進 CHANGELOG → `git commit -m "bump vX"` → 建立**本地** tag。

> **首次釋出會收整段歷史。** git-cliff 用最新的 `v[0-9]*` tag 界定「已釋出 vs 未釋出」;
> 在第一個 tag 出現前,`--unreleased` 會把**整段歷史**折進第一個版本區段,所以先前所有工作
> 都會被記進更新紀錄,不會遺漏。之後每次釋出只涵蓋自上個 tag 以來的 commit。

`make release` **不會 push**。確認無誤後:

```bash
git push origin HEAD --follow-tags
```

推上去的 tag(形狀為 `v20*`)會觸發
[`.github/workflows/release.yml`](https://github.com/HYChou0515/ai-workspace/blob/master/.github/workflows/release.yml):
它用 `git-cliff --latest` 取出該版區段當作 release body,再以
`softprops/action-gh-release` 發布 GitHub Release。因此 GitHub 與網頁看到的是**同一份**紀錄。

## 3. 版號絕不由 agent / CI 更動

bump 版號只發生在維護者手動執行的 `make release` 裡——CI 不跑、agent 不跑。日常開發的
commit **絕不**改 `pyproject.toml` 的 `version`,也**絕不**手改已生成的 CHANGELOG 版本區段
(它們會在下次 `make release` 時被重新生成)。發版是人的決定。
