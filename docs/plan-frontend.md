# workspace-app — Frontend Plan

You're the frontend agent. This brief is self-contained — you should
not need to read the backend plan to do your work, but it lives at
[`plan-backend.md`](./plan-backend.md) if you want context on the BE
items you depend on.

The BE-FE wire contract (SSE event types, HTTP routes) is documented
in **§4 Cross-cutting contracts** of `plan-backend.md`. **If you change
the FE side of the wire, mention it there too** so BE doesn't drift.

---

## 1. Context — what exists today

```
web/
├── package.json          # React 19 + Vite 6 + TypeScript 5, pnpm
├── tsconfig.json
├── vite.config.ts        # dev server :5173, proxies /workspace*, /conversation*, /agent-config to :8000
├── index.html
└── src/
    ├── main.tsx          # createRoot
    ├── App.tsx           # workspace switcher (just an input) + Chat
    ├── Chat.tsx          # transcript + composer + SSE consumer
    ├── events.ts         # AgentEvent type union + streamAgentEvents() generator
    └── styles.css        # bare-bones CSS
```

**Commands**
- `cd web && pnpm install`
- `pnpm run dev` — Vite dev server, hot reload, BE proxy on :5173
- `pnpm run build` — `tsc --noEmit && vite build`, writes `web/dist/`
  which the BE auto-mounts at `/` of FastAPI on :8000
- `pnpm run typecheck`

**What works now**

- User types in a workspace id (free-form text) + a prompt
- `Cmd/Ctrl+Enter` sends to `POST /workspaces/{id}/messages`, which
  returns `text/event-stream`
- `streamAgentEvents()` parses SSE, yields typed `AgentEvent`s
- Each event renders as a colored row in the transcript:
  - User message → blue
  - `MessageDelta` → green (whitespace preserved)
  - `ToolStart` → amber, monospace, shows `name({args})`
  - `ToolEnd` → yellow, monospace, shows output
  - `error` → red
  - `done` → grey italic divider

**Known wire shape** (mirrors `src/workspace_app/api/events.py`):

```ts
type AgentEvent =
  | { type: "message_delta"; text: string }
  | { type: "tool_start"; call_id: string; name: string; args: Record<string, unknown> }
  | { type: "tool_end"; call_id: string; output: string }
  | { type: "done" }
  | { type: "error"; message: string };
```

---

## 2. Sync rule

`web/src/events.ts` is the wire-format mirror of
`src/workspace_app/api/events.py`. Backend may add new event variants
(see §F4 / §F5 below); when they do, `events.ts` must follow in the
same change set, and the `TranscriptRow` switch in `Chat.tsx` must add
the new arm. Backend will update the contracts table in
`plan-backend.md` when it ships a new variant — that's your trigger.

Same rule for HTTP routes: see contracts table in `plan-backend.md`.

---

## 3. Open work

### F1  Workspace picker & creator  *(no BE dependency)*

Replace the free-form text input with a real picker.

**Spec:**
- On mount: `GET /workspace` (specstar auto-CRUD; returns
  `{data: [{resource_id, data: {name, description, attached_agent_config_id}}, ...]}` — verify the
  exact shape against `/docs`).
- Render the workspace list as a sidebar or dropdown. Clicking switches
  current workspace.
- "New workspace" button opens a small modal/inline form: name +
  optional description, POST to `/workspace`, prepend the result to
  the list, switch to it.
- Currently `workspaceId` is local state in `App.tsx` — keep it there,
  just hydrate from the API.

**Files:** new `web/src/WorkspaceList.tsx`, edits to `App.tsx`,
possibly a small `web/src/api/workspaces.ts` helper.

**Notes:**
- specstar's auto-CRUD response shape is `{data: ..., meta: ...}`
  (check `/docs` to be sure). If the field naming surprises you,
  type a minimal `WorkspaceResource` and convert.
- No tests required for the FE; manual smoke is enough.

---

### F2  Conversation hydration on workspace switch  *(no BE dependency)*

Right now switching workspaces wipes the transcript. Load existing
messages instead.

**Spec:**
- On `workspaceId` change: `GET /conversation` (filter client-side by
  `workspace_id` — the BE doesn't index it yet), find the one matching
  the current workspace, render its `messages` array into the
  transcript before subscribing to new events.
- Each message in the array has `{role, content, tool_call_id?,
  tool_name?}`. Map:
  - `role: "user"` → existing user row
  - `role: "assistant"` → `MessageDelta`-styled row
  - `role: "tool"` → `ToolEnd`-styled row
- If no conversation exists yet (new workspace), render empty.

**Files:** `Chat.tsx` (`useEffect` on `workspaceId`).

**Notes:** there's only one conversation per workspace (BE Q8). The
list scan is O(N workspaces) — fine for v1.

---

### F3  File browser pane  *(depends on BE §3.8 — Files API)*

Show the workspace's files alongside the chat. Click a file to view
its content.

**Spec:**
- Layout: 2-column on desktop (≥1024 px), files left, chat right.
  Collapse to tabs on narrow screens.
- File list: `GET /workspaces/{id}/files` returns `[{path, size}]`.
  Refresh on every `ToolEnd` event whose source tool was `write_file`,
  `delete_file`, or `exec` (cheap; specstar is in-memory in v1).
- File view: clicking a file calls `GET /workspaces/{id}/files/{path}`.
  Render text files in a `<pre>` (mono, scroll). Binary → show
  "binary, N bytes".
- (Optional, behind a config flag in the UI) inline edit via
  `PUT /workspaces/{id}/files/{path}`.

**Files:** new `web/src/FileBrowser.tsx`, layout shuffle in `App.tsx`.

**Blocked until:** BE ships §3.8. You can ship the component with
mock data first if you want, then swap to real fetch when the
endpoints land.

---

### F4  Stop button + RunCancelled rendering  *(depends on BE §3.2)*

When the agent is running, show a Stop button. Clicking it cancels the
in-flight turn.

**Spec:**
- BE §3.2 makes "POST a second message" cancel the first. Two
  approaches for the UI:
  - **Simple:** send a sentinel `POST /workspaces/{id}/messages
    {content: "/stop"}` and let BE recognise it. (Backend should
    confirm this is the protocol.)
  - **Cleaner:** dedicated `DELETE /workspaces/{id}/messages/current`
    endpoint. (Coordinate with BE — propose this in the contracts
    table.)
- When `RunCancelled` event arrives, render as a distinct row
  ("— cancelled —", red-ish, italic). Treat as terminal: clean up
  the AbortController, re-enable composer.

**New event to render** (mirror in `events.ts`):
```ts
| { type: "run_cancelled" }
```

**Files:** `Chat.tsx`, `events.ts`.

---

### F5  SandboxKilledIdle banner  *(depends on BE §3.3)*

When the BE emits `SandboxKilledIdle`, show a non-blocking banner
("Sandbox went to sleep — next shell command will cold-start"). The
event is non-terminal: the stream continues.

**New event to render:**
```ts
| { type: "sandbox_killed_idle" }
```

Plus the BE §3.6 refined events when they ship:
```ts
| { type: "tool_call_parse_error"; call_id: string; raw: string; hint: string }
| { type: "max_turns_exceeded"; turns: number }
```

`tool_call_parse_error` is non-terminal (a retry follows);
`max_turns_exceeded` is terminal.

**Files:** `events.ts`, `Chat.tsx`.

---

### F6  EventSource reconnect  *(depends on BE §3.7)*

Today `streamAgentEvents` uses `fetch` with a streaming body. If the
connection drops mid-run, the user loses the rest of the events. With
BE §3.7's `GET /workspaces/{id}/events?since=<last_event_id>` endpoint,
we can recover.

**Spec:**
- Track the last event id seen (BE will send `id:` lines in SSE per
  spec; coordinate the schema with backend in contracts table).
- On `fetch`/`ReadableStream` error mid-stream, retry with `GET
  .../events?since=<last>` once; if that also fails, surface
  `RunError`.
- Cap retries at 1; user can resend manually after.

**Files:** `events.ts`.

---

### F7  Polish — bottom of the priority list

- **Markdown rendering for `MessageDelta`.** Right now it's
  whitespace-preserved plain text. Use `react-markdown` (or similar)
  with `code-block` styling. Be paranoid: sanitize HTML.
- **Tool args display.** `JSON.stringify(args)` is ugly. Use a tiny
  syntax-highlighted block or a key/value table when args fit.
- **Tool output truncation + "show more"** for long stdout.
- **Keyboard:** Esc to cancel pending turn (when F4 lands); ArrowUp
  in empty composer to recall the previous prompt.
- **Workspace bar** lives in App.tsx as a plain input. After F1, hide
  the raw id and show the name.
- **Dark mode.** Pick up `prefers-color-scheme` from `styles.css`.

---

## 4. Order

1. **F1 Workspace picker** — independent, low risk, removes the
   "what's a workspace id" papercut.
2. **F2 Conversation hydration** — independent, makes refresh non-
   destructive.
3. **F3 File browser** — biggest UX win; ship UI with mocked data, swap
   to real fetch when BE §3.8 lands.
4. **F4 Stop button** — after BE §3.2.
5. **F5 New event renderers** — after BE §3.3 / §3.6.
6. **F6 Reconnect** — after BE §3.7.
7. **F7 Polish** — any time.

F1, F2, and F3-with-mock-data can ship immediately without waiting on
backend.

---

## 5. Conventions

- **TypeScript strict.** `tsconfig.json` already has `strict: true`,
  `noUnusedLocals`, `verbatimModuleSyntax`. Don't loosen these.
- **`pnpm run build` must pass** (which means `tsc --noEmit` passes)
  before committing.
- **No new heavy deps without a clear reason.** Current footprint is
  React + Vite only; keep it lean. Avoid `axios`, `react-router-dom`
  (use plain `fetch` and local state), and full UI kits.
- **CSS lives in `styles.css`** for now. If you need component-scoped
  styles, CSS modules are fine; don't bring in styled-components or
  tailwind without discussion.
- **Don't touch backend files.** If you need a route or event the BE
  doesn't expose, add a line to the contracts table in
  `plan-backend.md` under the appropriate `⏳` row and ping the
  backend agent.

---

## 6. Things you DO NOT need to worry about

- specstar admin UI is auto-generated at `/openapi.json` + `/docs`;
  it covers raw CRUD. The SPA is the chat-focused experience on top.
- Sandbox lifecycle, FS sync, idle kill — pure backend; you only see
  their event/route signatures.
- LLM choice / Ollama / LiteLLM — pure backend.
- TDD discipline on backend tests — pure backend.
