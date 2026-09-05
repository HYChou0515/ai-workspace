# WUI reference

Everything a page can do. `window.workspace` exists before your first line runs;
you do not create it, import it, or wait for it.

Every call returns a promise. A refusal **rejects with an `Error` whose message
is a sentence** — show that sentence; it is written for the person looking at the
page, and it is what they forward back to you.

## The seven calls

```js
await workspace.listFiles(prefix?)   // → { files: [{ path, size, read_only }] }
await workspace.readFile(path)       // → { path, kind: "text", text }
                                     //   or { path, kind: "binary", dataUrl }
await workspace.writeFile(path, text)// → { path }
await workspace.deleteFile(path)     // → { path }
await workspace.openFile(path)       // opens it in the workspace beside the page
await workspace.whoami()             // → { user }
await workspace.callTool(name, args) // → { output, exit_code }
await workspace.startRun(workflow, input, onEvent)  // → { run_id }
```

Plus one subscription, which is not a call:

```js
workspace.onFileChanged(function (path) { /* someone else edited `path` */ });
```

### Paths

A path starting with `/` is from the item's root. Anything else is **next to your
page**, which is what you want for your own data:

```js
await workspace.readFile("data.json");   // your folder
await workspace.readFile("/notes.md");   // the item's notes
```

`readFile`, `listFiles` and `openFile` reach the whole item. `writeFile` and
`deleteFile` reach **only your folder** — `"/notes.md"` and `"../notes.md"` are
both refused, and so is `/lot-tracker2/x` from inside `/lot-tracker`.

⚠️ **A path someone else gave you needs the leading `/`.** Bare means "next to
the page", so a workspace path handed over without one — which is how a tool
names a file — has the folder put on twice:

```js
// a tool answered with "lot-tracker/out.json"
await workspace.readFile("lot-tracker/out.json");  // reads /lot-tracker/lot-tracker/out.json
await workspace.readFile("/lot-tracker/out.json"); // what it meant
```

The doubled name in the error is the tell, and the refusal names both spellings
that would have worked.

**Put the page in a folder.** A view file at the workspace root has no folder of
its own, so it can read but every write is refused.

### Reading a file that may not exist

There is no "does it exist". A missing file rejects, and for **your own data
file** that is the normal first-run path — the pane stays quiet about it:

```js
const rows = await workspace
  .readFile("data.json")     // YOUR file, in your folder
  .then((f) => (f.kind === "text" ? JSON.parse(f.text) : []))
  .catch(() => []);          // first run — no file yet
```

⚠️ **Do not wrap somebody else's path in that `.catch`.** Absence is only
ordinary in your own folder. A path from elsewhere in the item — one a tool
handed you, one you built from a listing — that cannot be read is a mistake,
the pane says so, and swallowing it renders an empty page that says "nothing
found" forever over data that is right there. Show the error instead:

```js
const out = await workspace.readFile(pathFromTool).catch((e) => {
  show(e.message);           // the platform's sentence names what to change
  return null;
});
```

### Saving

`writeFile` replaces the whole file. Save on a real event (blur, a Save button, a
debounce), not on every keystroke: each write is broadcast to everyone else
looking at this item.

```js
let timer = null;
function save(rows) {
  clearTimeout(timer);
  timer = setTimeout(function () {
    workspace.writeFile("data.json", JSON.stringify(rows, null, 2)).catch(show);
  }, 500);
}
```

### Someone else editing

The last write wins — the platform does not merge. If your page holds state the
user typed, you must at least notice:

```js
workspace.onFileChanged(function (path) {
  if (path.endsWith("/data.json")) {
    // Reload if nothing is half-typed; otherwise say so and offer to reload.
    // Do NOT silently overwrite: you would erase their colleague's change.
  }
});
```

### Tools

`callTool` is the page's only way to reach anything outside this item. The tool
runs on the platform with the item's credentials — **your page never holds a
secret and cannot ask for one**.

**Which tools exist is per-app, and you cannot tell from the names.** The list is
appended to the end of `SKILL.md` when you read it, under "Tools this app offers
its WUIs". Your own toolset is not the answer: most of what you hold are
built-ins, which a page can never call.

Declare each tool in the view file, or the call is refused:

```yaml
view: wui
title: Lot tracker
tools: [lot-status]
```

```js
const res = await workspace.callTool("lot-status", { lot: "A1" });
if (res.exit_code !== 0) show(res.output);   // the tool ran and failed
let data;
try {
  data = JSON.parse(res.output);
} catch (e) {
  show(res.output);                          // it answered, just not in JSON
  return;
}
```

`exit_code !== 0` is the tool's own failure, not a platform error, and `output`
is verbatim — nothing is appended to it, so it is safe to parse.

**Whether it IS JSON is the tool's contract, and the platform makes no promise
about it.** `output` is the bytes the command printed: JSON for one tool, a
table for another, a sentence when there is nothing to report. This is why the
`try` above is not defensive padding — it is the only thing between a shape you
assumed and a blank page. **Run the tool yourself before writing the parser**;
SKILL.md, "Run the tool before you parse it", says what to look at.

### Work that takes minutes

`callTool` runs one command and answers once. When the thing you need is longer
— read a folder of records, ask an agent to judge them, write a summary back —
that is a **run**, and `startRun` is how a page begins one.

```js
const { run_id } = await workspace.startRun("judge", { lot: "A1" }, function (event) {
  // Called repeatedly WHILE it runs. Draw whatever you like with it.
  if (event && event.type === "step") show(event.text);
});
```

Three things follow from it being a run and not a bigger tool call:

- **It reports progress.** The promise settles at the end; `onEvent` fires
  throughout. A page that shows nothing for two minutes looks broken, and the
  person watching cannot tell it apart from one that is.
- **Closing the page does not lose the work.** The run keeps going and its
  result is written back, so "I got bored and closed the tab" costs nothing.
- **It is the same engine a schedule uses.** The only difference is whether
  somebody is watching.

⚠️ **Ignore events you do not recognise.** The platform's event set grows, and
your page will not be edited again. A handler that switches on every known type
and throws on the rest breaks the day a new one appears; one that ignores the
unknown keeps working forever.

Declare each workflow in the view file, exactly like a tool:

```yaml
view: wui
title: Scrap review
workflows: [judge]
```

A rejected `callTool` is a different thing again, and the message says which:

| message | what the reader must change |
|---|---|
| "did not declare X" | add it to `tools:` in the view file — yours to fix |
| "does not offer X" | this app does not grant it; an operator must add it |
| "X is unavailable: …" | the app grants it but it could not be resolved |

Show the message as it arrives. Collapsing these into "it failed" sends the
reader to the wrong place most of the time. `examples/external/` does this.

## Blocked, by design

`fetch` · XHR · WebSocket · `<script src="https://…">` · web fonts · remote
images · `<form action>` · opening a window · **navigating the page to another
site**. The page has no network at all. Anything you need is a file in the
folder, a `data:` URI, or a tool.

Files in your folder ARE reachable, by every ordinary spelling — `<script src>`,
`<link rel=stylesheet>`, `<img src>`, `<video src>`/`poster`, and `url()` inside
a stylesheet (linked or inline). They are read and folded into the page before
it runs, so they need no network either.

**`srcset` is not resolved.** Write one `src`.

## Errors

Uncaught errors, unhandled rejections, a file that failed to load and anything
the policy refused are all captured for you and shown in the pane — you do not
need a global handler. What you **do** need is to show the message from a
rejected `workspace.*` call somewhere the user can see, because that one names
something they can act on ("this page can only write inside its own folder").

## Labels for reporting

```html
<section data-wui="totals"> … </section>
```

When the user points at something, the report names the nearest `data-wui`
ancestor. Put one on each meaningful section and their "this bit is wrong"
arrives with the section's name.
