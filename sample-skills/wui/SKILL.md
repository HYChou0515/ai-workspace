---
name: wui
description: Build a WUI — a folder in this item's workspace that renders as a live, interactive page (a form, a board, a dashboard, a small tool). Use when someone describes work they redo by hand every time, wants to see this item's data laid out their way, or asks for a page/screen/form/dashboard.
---

# Build a WUI

A **WUI** is a folder in this item's workspace whose `*.ai.yaml` file says
`view: wui`. Opening that file runs the folder as a page. There is no publish
step and nothing to register — write the files and it works.

Read `reference.md` for the exact API before you write code. **Start by copying
one of the three complete, working examples in `examples/`** — they already get
right the things that are easy to get wrong, and copying beats generating.

| copy | when | what it shows |
|---|---|---|
| `examples/dashboard/` | the data already exists and somebody wants to SEE it differently | listing then reading in parallel, parsing files people hand-edit, `openFile` to hand the user back to the real file |
| `examples/editor/` | the page is where the data gets ENTERED or changed | saving without thrashing, hearing about someone else's edit without discarding what is half-typed, staying inside your own folder |
| `examples/external/` | the answer lives in ANOTHER system | `callTool`, and telling the three refusals apart — not declared / not granted / the tool itself said no |
| `examples/react/` | hand-written DOM has stopped paying | a real build (`pnpm build` → `dist/`), the three settings that fail silently without them, and the one step that can be forgotten |

If a page both reads and writes, start from the dashboard and add saving — a
page that reads wrongly is obvious, a page that writes wrongly is not.

**Prefer no build unless the page needs one.** Without one, the files you wrote
ARE the page: edit, press Refresh, see it. With one, the page is `dist/` and
editing `src/` changes nothing until you rebuild — so a change with no rebuild
leaves the user looking at the old page with nothing saying why. That is the
only silent failure on this path, and it is yours to avoid: **rebuild in the
same turn as the edit.** Libraries do not decide this — a UMD file in the folder
(`<script src="./chart.umd.js">`) is inlined like anything else, no build
needed.

⚠️ **The external example is the one you cannot copy unchanged.** Its tool has
to be one this app actually grants, and **"Tools this app offers its WUIs"** —
appended to the end of this skill when you read it — is the only place that says
which. You cannot tell from a tool's name: `read_file` and `lot-status` look
alike to you, and only one of them a page can call. If that section is absent,
say so and ask rather than guessing; a tool you invent fails at the call.

## What you are actually making

Someone who does not write software just described a job they redo by hand. The
WUI is that job, laid out. Two consequences:

- **Their words are the labels.** Use the vocabulary they used, not the field
  names in the files.
- **It has to work on the first look.** They cannot open a console. If the page
  is blank they can only tell you "it's broken", so prefer boring code that runs
  to clever code that might.

## The shape

```
lot-tracker/
  page.ai.yaml     ← view: wui — this is what makes the folder a WUI
  index.html       ← the entry
  app.js           ← behaviour
  style.css        ← looks
  data.json        ← whatever the page saves (your folder, your file)
```

A read-only page has no `data.json` — it reads what the item already holds.

`page.ai.yaml`:

```yaml
view: wui
title: Lot tracker
# tools: [lot-status]   # only if the page calls one — see reference.md
```

`title` is what the pane is called. `entry` overrides `index.html` if you must.

## The rules that are enforced (not advice)

- **No network AT RUNTIME.** `fetch`, XHR, WebSocket, a CDN `<script src>`, a
  Google Font — all blocked once the page is running. Everything it uses is a
  file in its folder, or comes through `workspace.*`. There is no workaround; do
  not spend a turn looking.
  **A build is not runtime.** `pnpm install` runs in the sandbox, where you have
  a network like any other command. What must not need one is the finished page.
- **Read anywhere in the item, write only your own folder.** `workspace.readFile`
  can read `/notes.md`; `workspace.writeFile` can only write under
  `lot-tracker/`.
- **`workspace.callTool` only reaches tools listed in `tools:`** in the yaml, and
  only ones this app grants. Declaring one you were not given fails at the call.

## Working on one

- Edit the individual file — `app.js`, not the whole page. That is why a WUI is
  a folder.
- **The page does not reload itself.** After you change a file, tell the user to
  press **Refresh** above the page.
- When they say it is broken, ask them to press **Report a problem** and click
  the part that looks wrong, then **Tell the agent**. You get the markup, the
  size and the computed styles — which is how you see a layout you cannot look
  at. Label your main sections with `data-wui="..."` so a report says which one.

## Before you say it is done

Open it yourself in your head, in this order:

1. Does `index.html` load `./app.js` and `./style.css` by relative path?
2. Is every URL in the page either relative or a `data:` URI?
3. Does the first render work with **no** data — an empty `data.json`, none at
   all, or an empty folder to read?
4. Does every `workspace.*` call have a `.catch`, and does the page show that
   text rather than going blank?

Then tell the user which file to open, in their words: "open **Lot tracker** in
`lot-tracker/page.ai.yaml`".
