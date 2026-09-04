---
name: wui
description: Build a WUI — a folder in this item's workspace that renders as a live, interactive page (a form, a board, a dashboard, a small tool). Use when someone describes work they redo by hand every time, wants to see this item's data laid out their way, or asks for a page/screen/form/dashboard.
---

# Build a WUI

A **WUI** is a folder in this item's workspace whose `*.ai.yaml` file says
`view: wui`. Opening that file runs the folder as a page. There is no publish
step and nothing to register — write the files and it works.

Read `reference.md` for the exact API before you write code. **Start by copying
one of the complete, working examples in `examples/`** — they already get
right the things that are easy to get wrong, and copying beats generating.

| copy | when | what it shows |
|---|---|---|
| `examples/dashboard/` | the data already exists and somebody wants to SEE it differently | listing then reading in parallel, parsing files people hand-edit, `openFile` to hand the user back to the real file |
| `examples/editor/` | the page is where the data gets ENTERED or changed | saving without thrashing, hearing about someone else's edit without discarding what is half-typed, staying inside your own folder |
| `examples/external/` | the answer lives in ANOTHER system | `callTool`, and telling the three refusals apart — not declared / not granted / the tool itself said no |
| `examples/chart/` | somebody wants to SEE the shape of the numbers | a real charting library, and the one build step that fetches it into the folder |
| `examples/react/` | **the default toolchain** | React + TypeScript, `wui.d.ts` (the bridge, typed), and the three build settings that fail silently without them |

If a page both reads and writes, start from the dashboard and add saving — a
page that reads wrongly is obvious, a page that writes wrongly is not.

**A library is a file in the folder, not a CDN.** "No network" is about
RUNTIME. `<script src="https://cdn…">` never arrives — but a UMD build sitting
next to the page is inlined like `app.js` is, and the SANDBOX has a network to
fetch it with (`npm pack chart.js@4`, copy the one file in; `examples/chart/`
does this as its build step, so opening the page is enough). **Do not hand-draw
a chart.** Axes that agree with their own scale, hit-testing, tooltips and tick
spacing are a lot of code to get wrong, and the library costs nothing at
runtime. Prefer small ones: the file is inlined into the document, so its size
is paid on every open.

## Write it in React and TypeScript

**Default to `examples/react/`** — React + TypeScript + Vite — for anything past
one screen of static markup. Two reasons, and the second is the real one:

- **A WUI cannot report its own bugs.** The person looking at it does not open a
  console; if the page is wrong they can only say "it's broken". So a mistake
  caught while building is worth far more here than in code somebody debugs.
  `src/wui.d.ts` types the whole bridge, and the build runs `tsc --noEmit` before
  Vite, so `JSON.parse(file.text)` against a binary file — which renders as the
  word `undefined` and nothing else — stops being shippable. Copy `wui.d.ts`
  unchanged; it describes the platform, not your page.
- **The build is no longer a cost the reader pays.** Opening a built page
  rebuilds it, with the output on screen (Auto-rebuild, on by default, beside
  **Rebuild**).

The other examples are still what you copy for the SHAPE of the page — reading
in parallel, saving without thrashing, telling three refusals apart. They are
written in plain JS to keep that shape readable. Take the logic from them and
the setup from `examples/react/`.

**Still rebuild in the same turn as the edit** (`pnpm build` in the page's
folder), and say that you did. Auto-rebuild covers the person who opens the page
LATER; it does nothing for the one watching it right now, who presses Refresh and
sees the old one. A type error fails the build, so `dist/` stays as it was and
the log says why — the page does not silently become wrong.

**Plain files are still right for a genuinely small page** — one screen, no
state worth naming. Then the files you wrote ARE the page: edit, press Refresh,
see it. Libraries do not decide this either way: a UMD file in the folder
(`<script src="./chart.umd.js">`) is inlined like anything else, no build needed.

⚠️ **The external example is the one you cannot copy unchanged.** Its tool has
to be one this app actually grants, and **"Tools this app offers its WUIs"** —
appended to the end of this skill when you read it — is the only place that says
which. You cannot tell from a tool's name: `read_file` and `lot-status` look
alike to you, and only one of them a page can call. If that section is absent,
say so and ask rather than guessing; a tool you invent fails at the call.

## Run the tool before you parse it

**A tool's output shape is the tool's contract, not the platform's.** `callTool`
hands you `{ output, exit_code }` — `output` is whatever bytes the command
printed, verbatim. It may be JSON, or JSON Lines, or a table a person was meant
to read, or an empty string. Nothing here checks, and nothing will tell you: a
page that guesses wrong renders blank or shows `undefined`, in front of somebody
who cannot open a console and can only report "it's broken".

**You hold the same tool.** A page can only call tools this item's agent can
call, so before you write a line of parsing:

1. **Call it, from this turn, with arguments a real user would send.**
2. **Read what came back.** Is it JSON at all? Is the list at the top level or
   under a key? Are the numbers numbers, or strings with units in them? Which
   field is the one the user actually named?
3. **Call it once more for the boring answer** — a lot that does not exist, a
   date with nothing in it. That path is most of what the page will show, and it
   is where a tool switches to prose or exits non-zero.
4. **Paste the real output into your code as a comment**, trimmed. It is the
   only record of what the parser was written against, and the next person to
   touch the page has no other way to find out.

Then write the parser against **that**, not against what the name suggested.

### When the tool answers with a PATH

A tool with a large result should not print megabytes to stdout — it writes a
file and prints the **path**. So `output` is a filename, the data comes back
through `readFile`, and two things go wrong quietly:

- **You parse the path.** `JSON.parse(res.output)` on `{"path": "out.json"}`
  succeeds and yields an object with no rows in it, so the page renders "nothing
  found" over a perfectly good answer.
- **The path is not where you think.** `readFile` reads THIS item's workspace,
  and the two spellings mean different things:

  | what the tool printed | what `readFile` reads |
  |---|---|
  | `/lot-tracker/out.json` | `/lot-tracker/out.json` — the item's root |
  | `lot-tracker/out.json` | `/lot-tracker/`**`lot-tracker/out.json`** — next to the page |
  | `out.json` | `/lot-tracker/out.json` — next to the page |
  | `/tmp/out.json` | `/tmp/out.json` **in the item**, not the sandbox's `/tmp` |

  A workspace path from a tool almost always arrives WITHOUT a leading slash,
  because that is how the workspace names files everywhere else — and read as a
  bare path it puts the folder on twice. That is the "no such file
  `/x/x/foo.json`" you will see, and the doubled name is the tell.

  **So give `readFile` a leading `/` for anything a tool named**, and keep bare
  paths for your own files. And do not `.catch(() => [])` around a read of
  somebody else's path: that catch is for YOUR OWN data file on its first run,
  and using it here turns a wrong path into a permanent, silent "nothing
  found".

So when a tool answers with a path: check whether the file is really in the
item's workspace, `readFile` it, and show the read's own error message when it
fails. A 13MB single-line JSON goes through the bridge fine (measured), so size
is not what you are debugging — the path is.

If you cannot run it — it needs an identifier you do not have, or the data is
not there yet — **say so and ask for one real example of its output.** Do not
guess a shape and ship it; the guess fails in front of the user, not in front of
you.

Whatever you learn, keep the guards from `examples/external/`: check
`exit_code !== 0` first and show `output` as-is when it is non-zero, and wrap
`JSON.parse` in a `try` that shows the raw text rather than blanking the page.
Those cover the day the tool changes under you.

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
  press **Refresh** above the page. On a page with a build there is **Rebuild**
  next to it, and an **Auto-rebuild** switch that is on by
  default — but Refresh alone never builds anything, so a `src/` edit you did
  not build is still an old page.
- When they say it is broken, ask them to press **Report a problem** and click
  the part that looks wrong, then **Tell the agent**. You get the markup, the
  size and the computed styles — which is how you see a layout you cannot look
  at. Label your main sections with `data-wui="..."` so a report says which one.

## Before you say it is done

Open it yourself in your head, in this order:

1. Does `index.html` load `./app.js` and `./style.css` by relative path? **Is
   there a stylesheet at all?** A page with none is not "unstyled", it is the
   browser's 1995 defaults — Times New Roman headings, a grey submit button —
   and that is the first thing the person who asked for it sees. With a build,
   the equivalent is an `import "./styles.css"` in the source; the bundler
   emits it and links it for you.
2. Is every URL in the page either relative or a `data:` URI?
3. Does the first render work with **no** data — an empty `data.json`, none at
   all, or an empty folder to read?
4. Does every `workspace.*` call have a `.catch`, and does the page show that
   text rather than going blank?
5. For every `callTool`: did you RUN it and read the output, or did you assume
   its shape? And does the page survive a non-zero exit and a non-JSON reply?

Then tell the user which file to open, in their words: "open **Lot tracker** in
`lot-tracker/page.ai.yaml`".
