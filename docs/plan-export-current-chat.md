# Plan — Export downloads the first chat, not the one you are reading

Reported: *"the Export button is dishonest, or it can't download the current chat — it
seems to only get the first one."*

Both halves are true, and the second is the worse one: the file you get back does not admit
which conversation it holds.

## What actually happens

Reproduced against a live backend — one Playground item, two chats, a distinguishable
marker seeded into each:

| chat | id suffix | `is_default` | seeded message |
|---|---|---|---|
| FIRST chat | `…a449` | `true` | `MARKER-FIRST-CHAT` |
| SECOND chat | `…fab4d9` | `false` | `MARKER-SECOND-CHAT` |

`GET /a/playground/items/{item_id}/export-chat` — the endpoint the Export button calls —
returns:

```
content-disposition: attachment; filename="playground-item:87f11c20-….chat.json"
  "title": "export probe",
      "content": "MARKER-FIRST-CHAT",
```

So, whichever chat is on screen:

1. **The wrong conversation comes back** — always the default (earliest-born) chat.
2. **The file cannot be told apart from the right one.** `title` is the *item* title
   (`export probe`), not the chat title (`SECOND chat`), and the filename is the *item* id.
   Nothing in the download says which conversation it is, while the button's tooltip says
   "Export this conversation". That is the dishonesty: a plausible file, quietly wrong.

## Root cause

The whole path is keyed on the **item**, never on the chat.

- `AgentPanel` calls `downloadChatExport(slug, investigationId)` — no chat id is passed,
  even though the multi-chat shell that drives the panel holds one
  (`UseItemChat = AgentState & { chatId: string }`).
- The route resolves the conversation with `locator.conversation_for(item_id)`, whose own
  docstring reads: *"The item's **DEFAULT** chat — the earliest-born free chat."*

This is a leftover from before an item could hold many chats. Every other chat-facing route
was moved to chat scope and this one was missed:

| route | scope |
|---|---|
| `PATCH /a/{slug}/items/{item_id}/chats/{chat_id}` | chat |
| `DELETE …/chats/{chat_id}` | chat |
| `POST …/chats/{chat_id}/messages` | chat |
| `GET …/chats/{chat_id}/stream` | chat |
| `GET/PUT …/chats/{chat_id}/todos` | chat |
| `GET/PUT …/chats/{chat_id}/goal` | chat |
| `GET …/items/{item_id}/export-chat` | **item** ← the odd one out |

The KB chat's Export is already correct: it builds the payload in the browser from the chat
it is displaying, titling it `chat.title || chat.name_hint || "chat"` and naming the file
from that. That is the shape to copy — the product already contains its own answer.

## Decisions

- **The route moves to chat scope and the item-level one is deleted.** No compatibility
  shim. The only caller in the repo is our own front end (plus its tests); no doc, workflow
  or third party references it. Two rules for one behaviour is how this drifted in the first
  place.
- **The payload's title becomes the chat's title**, falling back to the item's title for
  an unnamed chat — which is exactly what a single-chat item exported before, so the
  common case does not move. (The KB chat falls back to its first user message; an app
  chat has an owning item to name it after, and "Oven drift" beats "hi".) The `.chat.json`
  format is re-uploadable to a KB collection, so this also stops every export from one
  item arriving under the same name.
- **Workflow chats become exportable.** `conversation_for` deliberately never returned one;
  a chat-scoped route addresses whatever the caller can already read, and access stays
  gated by `read_chat` on the item. A conversation a person can read on screen is one they
  may download.
- **The filename is derived from the chat title**, and the server owns it: the browser
  takes it from `Content-Disposition` rather than building a second one, so the two ends
  cannot drift apart.
- **The header follows RFC 6266.** A `Content-Disposition` is latin-1 on the wire, so a
  Chinese chat title — the common case in this deployment — put into `filename="…"` raises
  `UnicodeEncodeError` while the response is written: the export 500s rather than failing
  politely. The header carries an ASCII `filename` plus `filename*=UTF-8''…`, and the
  browser prefers the latter. (Found by review, after the first implementation shipped the
  crash; the regression test names a chat 爐溫漂移檢討 and encodes the header to latin-1.)

## Phases

### P1 — the backend answers for the chat you asked about

- Move to `GET /a/{slug}/items/{item_id}/chats/{chat_id}/export-chat`; delete the
  item-level route.
- Resolve the conversation by id, 404 on a chat that does not belong to the item.
- Title the payload from the chat, not the item.
- `Content-Disposition` names the chat.
- Tests (written first): two chats with distinct messages — the export of chat B contains
  B's messages and B's title; a foreign chat id is a 404; the existing `read_chat`
  permission test still holds. Update the four existing tests that call the old path.

### P2 — the button exports what the panel is showing

- Thread the current chat id from the multi-chat shell into `AgentPanel`'s Export.
  Written as a required prop first, so a caller could not forget it. #739 landed the same
  prop meanwhile, **optional**, for its context gauge — a surface can have no chat of its
  own, and the gauge is simply absent there. That contract wins, and Export follows it the
  same way: the button is drawn only when there is a chat to name. A button that cannot
  name its chat could only ask the server to pick one, which is the defect this replaces;
  drawing nothing says so honestly, and a rendered Export still always carries a real id.
- `downloadChatExport(slug, itemId, chatId)`; the download takes its name from the
  server's `Content-Disposition`.
- Tests (written first): the Export button requests the URL carrying the *displayed*
  chat's id — the assertion that would have caught this in the first place.
- Definition of done includes a live check in a real browser: two chats, export from the
  second, open the file and see the second chat's messages. Unit tests cannot see a wrong
  id that is still a valid request.

**Result of that live check** (built bundle, real backend, Chromium driving the UI):
screen on "Solder paste question" → `Solder-paste-question.chat.json`, titled
`Solder paste question`, holding that chat's message; switch the switcher to
"Oven drift review" → `Oven-drift-review.chat.json` with that chat's message. The
download follows the screen, and the file names itself.

## Follow-up — the export carries the whole message, not three fields of it

**The file is for debugging.** Asked whether the model's own reasoning and the user ids in
a mention belong in a file that can be re-uploaded to a KB collection, the answer was "all
of it — that is what makes it debuggable". So the export is a faithful archive of the
conversation, not a sanitised artifact, and everything persisted on a message goes in.

That decides the shape: serialise each `Message` **generically** (`msgspec.to_builtins`)
rather than naming fields one at a time. Naming them is what created this defect's whole
family — a field is added, the export is not updated, and nothing says so. A generic dump
carries every future field for free, including the two #749 adds.

Confirmed by probe, not by reading: a `Message` carrying `reasoning`, `created_at` and a
`metrics` object dumps to 17 keys and survives `build_chat_export` → `parse_chat_export`
intact, with an unmeasured `completion_tokens` still `null` — **not** coerced to `0`, which
is the invented-number defect #748 exists to remove.

Two things this must keep:

- `role` and `content` stay strings, which the KB upload path validates.
- Absent stays absent. Nothing in the export may substitute a zero for "not measured".

### Why this no longer waits on #749

It was held for #749 (`feat/reply-provenance-748`), which persists the model that served,
the token counts and the timings on the message. Naming those two fields in the export
would have needed that PR to land first. Dumping the message generically does not: the
fields arrive on their own the moment #749 merges, and so does whatever is added after it.
The two branches share no file, so nothing here was ever a merge dependency.

## Not in scope

- **Which** chat the KB chat's Export downloads — it was already right, and is where the
  shape here was copied from. Its *filename* was not: JavaScript's `\w` is ASCII-only, so
  it folded every Chinese-titled chat down to `-.chat.json`. That is the same class as the
  header defect above, one file away, so it is fixed here rather than left as a lesson
  applied halfway.
- The `.chat.json` schema itself — same `{title, messages:[{role, content, tool_name}]}`.
- Exporting several chats at once, or any format other than `.chat.json`.
