# 給工程師的安裝 skill

這個資料夾裡是**一份給使用者的 skill**(`SKILL.md`),讓任何人在自己的編輯器
(Claude Code / opencode / codex / cline)裡使用這個平台上的工具。

## 為什麼是 skill,不是文件

一個想用工具的工程師,手上通常只有一樣東西:**那支工具的 GitLab repo 網址**。
要把它變成一個能用的 MCP server,中間還缺工具名稱、artifact 網址、runner image、
以及一串 docker 參數——那是四件他沒理由知道的事。

寫成文件的話,他得自己一項一項湊,而且湊錯的時候沒有人在旁邊。寫成 skill 的話,
**組裝這件事交給他的 agent 做**——agent 會讀 repo、查 CI、改設定檔、然後自己跑一次
確認。而使用者只需要做他本來就會的動作:裝一個 skill。

所以對他而言只有兩步:

1. 裝這個 skill。
2. 跟 agent 說「用這個 skill,repo 在這裡:<網址>」。

## 發出去之前要做的一件事

`SKILL.md` 裡有一個佔位符:

```
RUNNER_IMAGE = <<RUNNER_IMAGE>>
```

換成你這個部署實際發布的 runner image 位址,例如
`registry.example/ai-workspace/mcp-runner:2026.08`。

**這是唯一要改的地方。** 其餘四件事 skill 自己推得出來(工具名稱從 manifest 讀、
artifact 網址從 repo URL 推、docker 參數是固定的)。

沒改就發出去也不會壞掉——skill 會在看到佔位符時**停下來並說明**,而不是去 registry
上找一個看起來像的 image 來跑。有測試釘住這個行為。

runner image 怎麼建、`BUILDER_ID` 要填什麼,見
[`docs/deployment.md`](../docs/deployment.md) §15.7。

## 為什麼它花這麼多篇幅在講失敗

因為照著做的人是一個人。而且失敗會落在**三個不同的人**身上:

- **工具作者** —— 用錯 builder 建的、artifact 過期、description 太模糊
- **平台團隊** —— 權限、runner image
- **他自己** —— 沒裝 docker、沒設 token、設定漏了掛載

分辨不出來的話,他會去問錯的人,然後那一天就沒了。最麻煩的是 GitLab 的 `404`:
**「artifact 過期」和「你沒有權限看這個專案」回的是同一個東西**,而這兩者要找的人
不同。skill 裡給了唯一能分辨的方法(用瀏覽器開開看)。

## 改動時請一起看的測試

- `tests/tooling/test_tool_skill.py` —— skill 該講到的事
- `sandbox-host/tests/test_mcp_runner.py::test_the_skill_explains_the_warning_this_runner_prints`
  —— **真的跑一次 runner、抓它印出來的字**,再去 skill 裡找得到。改了錯誤訊息卻沒改
  skill,這條會紅。
