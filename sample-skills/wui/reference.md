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

**Put the page in a folder.** A view file at the workspace root has no folder of
its own, so it can read but every write is refused.

### Reading a file that may not exist

There is no "does it exist". A missing file rejects, and that is the normal
first-run path:

```js
const rows = await workspace
  .readFile("data.json")
  .then((f) => JSON.parse(f.text))
  .catch(() => []);          // first run — no file yet
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
const data = JSON.parse(res.output);
```

`exit_code !== 0` is the tool's own failure, not a platform error, and `output`
is verbatim — nothing is appended to it, so it is safe to parse (though whether
it IS JSON is the tool's contract, not the platform's).

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
