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
- **The payload's title becomes the chat's title**, with the KB fallback chain
  (`title → name_hint → "chat"`). The `.chat.json` format is re-uploadable to a KB
  collection, so this also stops every export from one item arriving under the same name.
- **Workflow chats become exportable.** `conversation_for` deliberately never returned one;
  a chat-scoped route addresses whatever the caller can already read, and access stays
  gated by `read_chat` on the item. A conversation a person can read on screen is one they
  may download.
- **The filename is derived from the chat title**, sanitised the way the KB export already
  sanitises it (`name.replace(/[^\w.-]+/g, "-")`), so the file names the conversation.

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
- `downloadChatExport(slug, itemId, chatId)`; filename from the chat title.
- Tests (written first): the Export button requests the URL carrying the *displayed*
  chat's id — the assertion that would have caught this in the first place.
- Definition of done includes a live check in a real browser: two chats, export from the
  second, open the file and see the second chat's messages. Unit tests cannot see a wrong
  id that is still a valid request.

## Not in scope

- The KB chat's Export (already correct).
- The `.chat.json` schema itself — same `{title, messages:[{role, content, tool_name}]}`.
- Exporting several chats at once, or any format other than `.chat.json`.
