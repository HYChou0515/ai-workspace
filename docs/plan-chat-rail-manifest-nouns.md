# Plan — the chat rail says "chat" for everything, duplicates the platform menu, and hides its own dropdown

Three reports on the chat-first left rail (`ChatListRail`). Independent causes, one branch:
they are all in the same component and two of them are the same disease.

**Note on reproduction**: no app in this repo declares `primary_surface: "chat"` — the ones
here are `ide` (rca) and `views` (pm). The chat-first surface is enabled by a deployment's
own `app.json`. So every fix must be manifest-driven, and the tests must construct a chat
manifest rather than lean on a bundled app.

## Report 1 — the rail hardcodes "chat" for every App

`ChatListRail` calls the item a "chat" in six user-visible places, while the manifest
already declares what an item is called (`item.noun` / `noun_plural` / `create_label`):

| line | today | should follow |
|---|---|---|
| 127 | `+ New chat` | `item.create_label` (e.g. "New Project") |
| 180 | `My chats` | `item.noun_plural` |
| 199 | `No chats yet` | `item.noun_plural` |
| 100 | `aria-label="Show chats"` | `item.noun_plural` |
| 132 | `aria-label="Collapse chats"` | `item.noun_plural` |
| 252 | `aria-label="Rename chat"` | `item.noun` |

`AppDashboard.tsx:173-175` already does exactly this:

```tsx
const createLabel = manifest.item.create_label ?? `New ${manifest.item.noun}`;
const noun = manifest.item.noun_plural;
```

So the resolution rule exists — it is just written inline in one page. Extracting it (rather
than copying those two lines into the rail) is the point: a second copy is how the two
surfaces drift, which is precisely Report 2.

"Shared with me" stays as it is — it describes the sharer, not the item.

## Report 2 — the platform menu is two lists, and the rail's is missing entries

The app list IS shared (`useApps()` in all three surfaces). The **platform links are not**:
`ChatListRail` has a hardcoded `PLATFORM_LINKS` array, `GlobalNav` hand-writes each
`<FixedLink>`. They have already drifted:

| link | GlobalNav | chat rail |
|---|---|---|
| Knowledge base / Review / Diagnostics | yes | yes |
| Help | — | yes |
| **My resources** | yes (everyone) | **missing** |
| **Groups** | yes (`isSuperuser \|\| myGroups.length > 0`) | **missing** |
| **Work calendar** | yes (`isSuperuser`) | **missing** |

The rail also has no i18n (a literal `"Review"` where GlobalNav uses `t("review.title")`)
and no gating, so it cannot express the conditional entries even if they were added.

This is a real capability gap, not cosmetics: in chat mode a person cannot reach their own
resources at all.

**Fix shape**: one module owns "the platform destinations for this viewer" — the list plus
its `isSuperuser` / `groups` conditions — and both surfaces render from it. Not a shared
constant array (that would still leave the conditions duplicated).

## Report 3 — the chat switcher's dropdown renders under the rail

Three stacking numbers, defined in three places, with no shared scale:

```
.chat-switcher__menu   z-index: 20     styles/topic-hub.css:45
.chat-rail             z-index: 40,41  styles/chat-rail.css
Popover                zIndex: 50      components/Popover.tsx:63
```

20 < 40, so the switcher's menu is painted under the rail whenever the rail is expanded.
Every other menu escapes because the shared `Popover` sits at 50.

**Fix shape**: the switcher's menu is a popover-class surface and belongs at the popover
layer, not below the furniture. Raising one number would fix the symptom; the reason it
happened is that the layers are magic numbers scattered across files, so the fix should
name the scale in one place (tokens) and have these three refer to it.

## Phases

1. **Platform destinations, one source.** Extract the list + conditions; render GlobalNav
   and the rail from it. Guard: a test that fails when one surface can reach a destination
   the other cannot.
2. **Rail follows the manifest.** Extract the noun-resolution rule from `AppDashboard`,
   use it in the rail. Guard: render the rail with a manifest whose noun is "Project" and
   assert no "chat" wording appears in the item affordances.
3. **One stacking scale.** Define the layers as tokens; point rail / switcher / popover at
   them. Guard: a stylesheet test asserting the switcher's layer is above the rail's — the
   relationship, not the literal numbers (the same shape as the existing contrast guards).

Order: 1 and 2 are independent; 3 is independent of both. Each is separately shippable.
