---
name: agent
description: Write an agent — an agent.ai.yaml that other people start conversations with from Agent Hub, each in a copy of this workspace. Use when someone wants a chat assistant for others (a helper for a team, a question-answerer about this item's data), or asks to publish one, change one, or write its release note.
---

# Write an agent

An **agent** is one file, `agent.ai.yaml`, in any folder of this workspace. Once
someone publishes it, it is in **Agent Hub**, and every person who starts a
conversation with it gets **their own copy of this workspace** to talk in — the
files as they were when it was published, running as this agent.

It is not a sub-agent (`.agent/<name>/AGENT.md`, which `run_agent` calls from
inside a turn) and not a WUI (a page). Do not confuse the three.

## The file

```yaml
view: agent
title: Sales helper                 # its name in Agent Hub — required
description: Answers questions about this quarter's orders.
icon: chart                         # a file in this folder, an emoji, or a platform icon name
color: "#3B82F6"
instructions: |                     # required: what makes it this agent
  You help the sales team with the order files under data/.
  Always cite the order id you read an answer from.
  Answer in the language the question was asked in.
tools: [read_file, list_files, ask_knowledge_base]   # optional — see below
suggestions:                        # what a new conversation opens with
  - Which orders shipped late this week?
  - label: Monthly summary
    prompt: Summarise this month's orders by customer and status.
publish:                            # what the Publish agent dialog opens with
  reach: everyone
```

- **`instructions:` is the agent.** It is added to this App's own instructions
  for every turn of a conversation with it. Write what the person talking to it
  needs: what it knows about, where that lives in the files, how to answer.
- **`tools:` can only narrow.** It is intersected with the tools this App gives
  this workspace; a name the App does not grant is simply not there (the agent
  panel names it). Leave it out to keep all of the App's tools. There is no key
  for a model, a preset or a new tool — an agent cannot have more than the App.
- **Its skills and workflows** are the workspace's own plus the ones in its
  folder: `<folder>/.skill/<name>/SKILL.md` and `<folder>/.workflows/<id>.json`.
  One in its folder wins over the root's of the same name.
- A key the file does not know is named in the panel and stops publishing — a
  typo like `instruction:` would otherwise be an agent running without it.

## Try it before anyone else does

Tell the user to open the file: it shows the **agent panel** — the tools and
skills a conversation will have, anything wrong with the file, and whether it is
published. **測試對話** (Test chat) opens a chat beside it that answers as this
agent in their own workspace. Ask them to try the questions the people who will
use it would ask, and fix the instructions from what they see.

## Publishing: ask, then they press

**發布 agent** (Publish agent) is the user's press — someone who may manage this
workspace's sharing — never yours. Ask only what they have not already said:

1. **Who may use it** — `reach`: `me`, `subjects` (with `subjects:` listing
   `user:<id>` / `group:<id>`) or `everyone`.
2. **The sandbox size of each conversation's copy** — `cpu_cores` and
   `memory_gb`, or leave them out for the platform's default.
3. **On a re-publish, what changed** — draft a one- or two-line release note
   from what you changed since the last publish ("Answers now cite the order
   id.") and give it to them to paste or edit in the dialog; people see the
   latest few on the agent's page.

Write the answers into `publish:`, then summarise in a sentence and tell them to
press **發布 agent**. Say two things plainly:

- Each conversation copies this workspace as it is now, minus run state and
  schedules. A token in the workspace's **Shared** values goes into every copy —
  a personal credential belongs in each person's own environment instead.
- Publishing again changes what NEW conversations start from; a conversation
  already going keeps the copy it has.
