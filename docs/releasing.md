# 發布與更新紀錄（Releasing & Changelog）

平台的更新紀錄(release notes)同時出現在兩個地方——GitHub 的 Releases 頁,以及網頁的
**說明 → 更新紀錄**(`/help/releases`)——而且兩邊內容**同源**:都由
[`src/workspace_app/help_content/CHANGELOG.md`](https://github.com/HYChou0515/ai-workspace/blob/master/src/workspace_app/help_content/CHANGELOG.md)
衍生。這份 CHANGELOG 由平台內建的產生器
[`python -m workspace_app.changelog`](https://github.com/HYChou0515/ai-workspace/blob/master/src/workspace_app/changelog/render.py)
從 git 歷史生成——**每個合併的 PR 一條**(以 `git log --first-parent` 走訪),而非每個 commit。
PR 分支上的中繼 commit(`P1 …`、`P2 …`)是實作過程,不會進更新紀錄。(#441)

> 對照閱讀:[開發者指南](development.md)(commit 規範與品質 gate)。

---

## 1. 一條紀錄 = 一個合併的 PR,所以 **PR 標題**要規範化

產生器走 `git log --first-parent`,對每個節點取一條「有效訊息」:

- **PR 合併 commit**(GitHub 的 `Merge pull request #N …`):它的 **body 第一行就是 PR 標題**,
  拿來當那條紀錄。所以真正決定更新紀錄的是 **PR 標題**,請把它寫成規範化 commit。
- **squash-merge / 直接落在發布分支的 commit**:用它自己的 subject。
- PR 分支內的中繼 commit 落在 merge 的第二親上,first-parent 走訪不會碰到,**自動被略過**。

有效訊息再依前綴分類:

| commit 前綴 | 更新紀錄分類 | 網頁預設顯示 |
| --- | --- | --- |
| `feat:` | Added(新增) | ✅ 重點 |
| `fix:` | Fixed(修復) | ✅ 重點 |
| `perf:` | Performance(效能) | ✅ 重點 |
| `refactor:` | Changed(變更) | 只在「詳細」 |
| `docs:` | Documentation(文件) | 只在「詳細」 |

不符合格式的訊息(例如 `--`、`wip`、無 body 的 `Merge branch …`)以及
`chore` / `ci` / `test` / `style` / `bump` **會被略過,不會出現在更新紀錄**。scope 慣例是
issue 編號,例如 `fix(#465): …`;產生器會把它渲染成結尾的 `(#465)`。

網頁的
[`/help/releases`](https://github.com/HYChou0515/ai-workspace/blob/master/web/src/pages/ReleasesPage.tsx)
預設只顯示「重點」(feat/fix/perf),可切到「詳細」看全部;`[Unreleased]` 區段永遠不會顯示。

```bash
make changelog-preview          # 預覽自最新 tag 以來、尚未釋出的 PR 紀錄(不寫檔)
```

## 2. 釋出(維護者操作)

版號採 **CalVer**:`vYYYY.MM.DD`,同一天再發一版時自動加後綴 `.1`、`.2`……
(例:`v2026.07.06` → `v2026.07.06.1`)。由維護者執行:

```bash
make release
```

它會:偵測今天的版號(含同日後綴)→ 把 `pyproject.toml` 的 `version` bump 成 PEP 440
正規化後的值(例 `2026.7.6`)→ 用內建產生器**重生整份 CHANGELOG**(把自上個 tag 以來的 PR
收成新的 `## [X]` 區段放最上面)→ `git commit -m "bump vX"` → 建立**本地** tag。

> **每次都從 git tags 全量重生,首次釋出會收整段歷史。** 產生器以各個 `v[0-9]*` tag 劃分版本
> 區段;最舊(第一個)區段涵蓋**整段歷史**,所以先前所有 PR 都會被記進更新紀錄,不會遺漏。
> 區段日期直接由 CalVer 版號推導(非時鐘),所以永遠與版號一致、不受時區影響。

`make release` **不會 push**。確認無誤後:

```bash
git push origin HEAD --follow-tags
```

推上去的 tag(形狀為 `v20*`)會觸發
[`.github/workflows/release.yml`](https://github.com/HYChou0515/ai-workspace/blob/master/.github/workflows/release.yml):
它直接從已提交的 `CHANGELOG.md` 用 `awk` 切出**最上面那個版本區段**當作 release body(不需要
任何工具鏈),再以 `softprops/action-gh-release` 發布 GitHub Release。因此 GitHub 與網頁看到的是
**同一份**紀錄。

## 3. 版號絕不由 agent / CI 更動

bump 版號只發生在維護者手動執行的 `make release` 裡——CI 不跑、agent 不跑。日常開發的
commit **絕不**改 `pyproject.toml` 的 `version`,也**絕不**手改已生成的 CHANGELOG 版本區段
(它們會在下次 `make release` 時被重新生成)。發版是人的決定。
