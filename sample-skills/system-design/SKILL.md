---
name: system-design
description: How this platform works and why it was built that way — its design docs and plans. Use when the user asks how some part of the system behaves or why, when they seem to expect something the system does not do, or before you explain a feature's rules.
readonly: true
---

# How the system works, from its own docs

A user who pictures the system differently from how it is built will be surprised by it,
and a guess from you makes that worse. The platform's docs and design plans are in this
workspace, at the version deployed now. Answer from them.

## What is here

- `.skill/system-design/docs/`: every doc and plan, exactly as deployed. This copy is
  readonly. The platform keeps it in step with each release, so never edit it.
- The index of the **current docs** (how the system works now) is the `nav:` section of
  `.skill/system-design/mkdocs.yml`: one title per doc.
- The index of the **plans** (each design decision and why it was made) is
  `.skill/system-design/docs/design-history.md`: one line per plan.

All of it is far too large to read at once. Read an index first, then only the few files
the question needs.

## How to look something up

1. **Delegate if you can.** When you have `run_agent`, hand the question to the
   `docs-reader` sub-agent, so the long reading stays out of this conversation.
   - If `run_agent` does not list `docs-reader`, save it first with `save_subagent`.
     Take the `name`, `description`, `tools` and body from
     `.skill/system-design/references/docs-reader.md`. When you hold `exec`, add it to
     `tools`, so the sub-agent can search with `grep`.
   - In the prompt, give the user's question in full and say what you need back.
2. **Otherwise, read it yourself.** Read the two indexes, then the files they point to.

## A plan that was overturned

A plan may begin with a line such as `> 被 #861（plan-marking-tuples.md）推翻`. It means a
later plan reversed some of its decisions. Read that later plan too before you answer.
Where the two disagree, the later one is how the system works now, and the current docs
agree with it.

## Answering the user

- Say it in the user's terms: what they see, what they do, and what happens. Name an
  internal module or file only when they ask for it.
- Say which doc it comes from, briefly, so they can read further.
- When the docs do not cover it, say so. Do not fill the gap with a guess.
