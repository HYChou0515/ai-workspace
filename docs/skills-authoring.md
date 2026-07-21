# 共創 skills（#298）

> 這篇是 **user 端**（跟 AI 共創、存進 workspace）的流程。想看三個擴充面（tool / skill /
> workflow）× dev/user 的全景,以及 **dev 端**把 skill commit 進 `sample-skills/` + 註冊
> `SHARED_SKILLS` 的那條路,請先讀 [`extending-the-platform.md`](extending-the-platform.md)。

**skill** 是一份簡短、可重複使用的指令檔，agent 會在需要時即時載入。它記錄的是
*你想要某一類任務怎麼被完成*——你分析流程的步驟、你的術語、你偏好的輸出風格——
這樣下次同樣的工作就會照你的方式走，不必再重新解釋一遍。這建立在開發者撰寫 skill 的機制
（#29）之上:同樣的 `SKILL.md` 格式、同樣的 `read_skill` 漸進式揭露(progressive
disclosure),只是現在改成在執行期由**使用者 + AI 共同創作、可攜帶、可升級**。

## 流程(會發生什麼)

在任何 workspace app(RCA、Topic Hub、Playground)裡,只要告訴助理你想做一個 skill——
例如 *「幫我做一個用來分流 reflow 缺陷的 skill」*。agent 會載入內建的 **`author-skill`**
meta-skill,走一個六步流程:

1. **界定範圍 + 觸發條件(Scope + trigger)** — 這是給哪一個任務用的,以及什麼時候該觸發
   (這個 skill 的一行 description)。
2. **抽取(Extract)** — 你的流程(有序步驟)、術語、與輸出風格。它也會讀 workspace 裡
   已經存在的東西(檔案、先前的訊息),從中挖出一個實際的範例,而不是只靠提問。
3. **草擬(Draft)** — 一份標準格式的 `SKILL.md` 內文。
4. **審閱(Review)** — 它把草稿給你看,反覆迭代到你核可為止。
5. **儲存(Save)** — 它呼叫 `save_skill`,寫出帶有正確 frontmatter 的
   `.skill/<name>/SKILL.md`(這個檔你永遠不必手動編輯)。
6. **收尾(Close out)** — 這個 skill 現在用 `read_skill('<name>')` 就會載入;它會告訴你
   怎麼下載/重用/升級它。

## skill 存在哪裡

每個 workspace 一份,放在 FileStore 的 `.skill/<name>/`:

```
.skill/
  triage-reflow/
    SKILL.md           # frontmatter（name + description）+ 方法論本體
    references/        # 選用 — agent 在需要時讀的額外文件
      defect-glossary.md
    scripts/           # 選用 — agent 透過 exec 執行的小段 Python
      summarise.py
```

- **References** 就只是檔案,當內文指到它們時(`see references/defect-glossary.md`)
  agent 用 `read_file` 讀。沒有特殊處理。
- **Scripts** 透過 workspace 內建的 Python stack 執行——
  `exec(["python", ".skill/<name>/scripts/summarise.py", "data.csv"])`——它帶了
  pandas / numpy / scipy / matplotlib。缺的套件可以在 sandbox 裡 `pip install` 補上——`pip`
  和 `python` 是同一個直譯器——但那只是**這個 workspace 當下的狀態**:沒有鎖檔、不可重現、
  workspace 一被回收就沒了,所以 script 若依賴它就得自己在開頭裝。如果它需要**釘死的**自訂
  依賴,或你想要一個經過驗證、可重複使用的 tool,那就是把它升格成正式 tool-package 的時機
  (見 `docs/plan-skills-and-tools.md` §B):tool-package 的依賴由 `uv.lock` 釘死並在 prebuild
  時打包進 bundle。

你儲存的 skill 會在同一個 workspace 立即載入(索引每個 turn 都會重新整理)。它**不會**
外洩到其他 workspace——那正是 download/import 的用途。

## Skills 面板

IDE 的檔案樹會把 `.skill/` 這個點開頭的資料夾藏起來,所以 chat header 裡的 **Skills** 按鈕
會開一個面板,列出這個 workspace 的所有 skill。從那裡你可以:

- **Download** 一個 skill,以它的資料夾 zip 形式下載——拿去別處重用,或交給團隊烤進
  起始的 profile。
- **Import** 一個你先前下載的 skill 資料夾到這個 workspace。

## 在別處重用一個 skill

1. 從 Skills 面板 download 該 skill(一個它 `.skill/<name>/` 資料夾的 zip)。
2. 在本機解壓縮。
3. 在另一個 item 的 Skills 面板,**Import** 那個解壓後的資料夾。它會在那裡立即載入。

## 把一個 skill 升級進起始 profile

當一個 skill 穩定了,而你想把它內建給某個 app 的所有人:

1. 從 Skills 面板 download 它的資料夾。
2. 交給團隊——由開發者把它 commit 進
   `apps/<slug>/profiles/<profile>/.skill/<name>/`,之後該 profile 的每個新 workspace
   都會內附它。

內建的 skill **會帶著它的整包檔案**——`references/`、`scripts/`、資料檔都一樣。第一次有人
用到它(送 Apply 或 agent 自己 `read_skill`)時,那些檔案就會被複製進該 workspace 的
`.skill/<name>/`,所以 `SKILL.md` 裡寫的 `see references/glossary.md` 和
`exec(["python", ".skill/<name>/scripts/x.py", …])` 都能直接成立(#589)。

複製之後那份就是**這個 workspace 的**:agent 可以改它、微調 script——這正是 skill 和
tool-package 的差別所在。因此:

- **平台永遠不會覆寫它。** 再次使用該 skill 只會確認檔案在,不會把出貨版本蓋回去。
- 你之後改進了出貨版本,既有的 workspace **不會自動拿到**。使用者在 Skills 面板按
  **更新**才會拉——而且是**逐檔**的:沒被動過的檔案更新,改過的原封不動保留並列出來給你看。
  要全部推翻用**還原成出廠版**,那是明確的破壞性動作。
- 只有 `SKILL.md`、沒有任何附帶檔案的 skill **不會**被複製——複製品會跟 package 版一模一樣,
  卻換來「內文不再跟上游」這個代價。

**執行權限不會被保留。** FileStore 沒有 mode 的概念,所以 script 一律用直譯器叫
(`python …` / `bash …`),不要依賴 `./script` 這種寫法。需要釘死依賴、或真的需要一個
預編譯執行檔時,那是升格成 tool-package 的時機(`docs/plan-skills-and-tools.md` §B):
它的依賴由 `uv.lock` 鎖住,而且它的 bundle 傳輸會保留權限位。

## 給 app 作者

內建 skill 的引入方式和 tool-package 一樣(見 `docs/plan-issue-298.md` Q7):把原始碼放到
`sample-skills/<name>/` 底下,在 `workspace_app.apps.shared_skills.SHARED_SKILLS` 註冊它,
然後在某個 app 的 `app.json` `agent.skills` 裡列出這個名字來把該 app 納入(並在
`agent.tools` 授予 `save_skill`)。`author-skill` 自己就是這樣出貨的,並被 RCA、Topic Hub、
Playground 納入。
