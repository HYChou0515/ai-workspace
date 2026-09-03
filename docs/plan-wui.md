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
   - **First-party tools are NOT reachable.** Not for semantic reasons — those a
     whitelist could fix — but for *type* reasons: `read_file_impl` truncates by
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
    CSP, the folder write scope, and the capability declaration.
11. **The agent learns WUI from a shared skill, not the system prompt.** A skill
    is a folder (`SKILL.md` + `references/` + a complete runnable example) whose
    body loads on demand, so a turn that never mentions WUI pays two lines. The
    example is deliberately an **editor**, the shape that trips on every hazard
    at once (debounce, `file_changed`, folder scope) — copying beats generating
    for small local models.
    - The list of tools this app exposes to `callTool` is **dynamic** and cannot
      live in the skill. The model cannot tell `data-fetch` from `read_file` by
      name, so the host must name the WUI-callable set explicitly.
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
    with one line in its `agent.skills` when it is ready. (Declaring it and
    turning it off is not the same thing: a shared skill is default-ON unless the
    profile pins an explicit `skills` list, and pinning one for `rca/default`
    would change the defaults of four unrelated skills to hide this one.) No
    feature flag, nothing to remove later. Note the asymmetry: the renderer ships
    to everyone, so a hand-written `view: wui` file already works. What is gated
    is whether the *agent* proposes one, not whether the platform can run one.

## Phases

Flat integers, one commit each.

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

## Deliberately not built

- Auto-reload, live data push, write throttling, tool concurrency limits (10).
- Screenshots and source maps in the pick report (13).
- Entity access from the bridge (14).
- Cross-item access, `exec`, and asking the agent from inside a page — the last
  would arrive as a tool if it ever does (7).
- Any second manifest format, registry, or "publish" action (2, 5).
