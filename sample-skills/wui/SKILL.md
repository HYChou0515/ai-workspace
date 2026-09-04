---
name: wui
description: Build a WUI — a folder in this item's workspace that renders as a live, interactive page (a form, a board, a dashboard, a small tool). Use when someone describes work they redo by hand every time, wants to see this item's data laid out their way, or asks for a page/screen/form/dashboard.
---

# Build a WUI

A **WUI** is a folder in this item's workspace whose `*.ai.yaml` file says
`view: wui`. Opening that file runs the folder as a page. There is no publish
step and nothing to register — write the files and it works.

Read `reference.md` for the exact API before you write code. There is a complete,
working example in `example/`; **start by copying it**, because it already gets
the three things right that are easy to get wrong (saving without thrashing,
hearing about someone else's edit, staying inside your own folder).

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

`page.ai.yaml`:

```yaml
view: wui
title: Lot tracker
# tools: [lot-status]   # only if the page calls one — see reference.md
```

`title` is what the pane is called. `entry` overrides `index.html` if you must.

## The rules that are enforced (not advice)

- **No network.** `fetch`, XHR, WebSocket, a CDN `<script src>`, a Google Font —
  all blocked. Everything the page uses is a file in its folder, or comes
  through `workspace.*`. There is no workaround; do not spend a turn looking.
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
3. Does the first render work with **no** saved data — an empty `data.json`, or
   none at all?
4. Does every `workspace.*` call have a `.catch`, and does the page show that
   text rather than going blank?

Then tell the user which file to open, in their words: "open **Lot tracker** in
`lot-tracker/page.ai.yaml`".
