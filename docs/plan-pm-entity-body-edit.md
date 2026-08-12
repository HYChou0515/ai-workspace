# Plan — a person cannot edit an entity record's body, and the board card hides its number

Two reports, one of them not yet diagnosed. They are unrelated except that both live in
the PM app's views, so they share a branch and nothing else.

## Report 1 — "the issue body cannot be edited; the AI can, I can't"

Confirmed with the reporter: the **Edit** button is reachable, the form opens, and the
markdown body **cannot be typed into**.

### What the code says

The editor exists and is wired end to end. The write path is not missing — it is *gated*:

```
EntityFileEditor.tsx:175   readOnly={!canWrite}          # the Monaco body editor
  ← EntityRecordPane / EntityRecordModal / AiYamlRenderer   pass canWrite through
    ← useItemCanWrite(slug, itemId)                      hooks/useItemCanWrite.ts
      ← useItemAccess(item).canWrite                     hooks/useItemAccess.ts:83
        ← canWriteItem(perm, me, item.created_by, isSuperuser, groups)
                                                         lib/itemPermission.ts:130
```

`canWriteItem` reads as correct, and in this order:

1. superuser → true
2. `me === created_by` → true (the owner controls their resource)
3. no permission, or `visibility: public` → true
4. `visibility: private` and not the owner → **false**
5. `restricted` → true only if a write verb grants this user or one of their groups

The API half is fine too: `PUT .../entities/{type}/{number}` takes a `body`
(`schemas.py::_EntityUpdateBody`) and passes it to `store.patch` (`entity_routes.py:242`);
`api/real.ts::getAppItem` does lift `created_by` off `revision_info`.

**Why the AI is not blocked by any of this**: the agent writes through the entity tool
into `EntityStore.patch` directly. `canWrite` is a *UI* gate — the asymmetry the reporter
noticed is exactly what the layering predicts, and is not itself the bug.

### The four candidates, and why only data can separate them

Every one of these produces the reported symptom, and the code cannot tell them apart:

| # | Cause | Would mean |
|---|---|---|
| A | The item was created by someone else (or by a seeding path) and is `private` | Working as designed; the bug is that nothing SAYS so |
| B | `created_by` comes back empty | Owner-bypass silently never fires — a real bug |
| C | `me` and `created_by` are not the same shape of id (SSO is not wired yet) | Owner-bypass never matches — a real bug |
| D | `restricted` without a write verb for this user | Working as designed; same disclosure gap as A |

**Phase 1 answers this before any code is written.** Guessing here would mean fixing a
permission check that is correct, or "fixing" a disclosure gap that is really a lookup bug.

### Phase 1 — RESULT: the permission theory is dead

Two findings, and together they close this line of enquiry.

**`created_by` is present.** Probed against a running app: the envelope carries
`revision_info.created_by = "alice"` (and `meta.created_by` too), which is exactly what
`api/real.ts::getAppItem` reads. **Candidate B is out.** The same probe confirms a freshly
created item is `visibility: private` — so the owner bypass is the ONLY thing letting its
creator write, which is what made B and C plausible in the first place.

**But `canWrite` must already be true.** `EntityRecordView.tsx:60` renders the Edit button
as `{canWrite && (…)}` — with `canWrite` false there is no button to press. The reporter
presses it. Therefore `canWrite === true`, `readOnly={!canWrite}` is not engaged, and
**A, C and D are out as well**.

That also disposes of the framing this plan started from: the AI/human asymmetry is not a
permission difference. Both sides are allowed to write. The block is somewhere in the
editing surface itself.

**Remaining candidates**, all inside the form rather than around it:

| # | Cause | Distinguishing symptom |
|---|---|---|
| E | The Monaco body editor fails to mount / initialise | the FIELDS above it still accept typing |
| F | The surface being used is not `EntityFileEditor` at all | there is no separate body area, only frontmatter fields |
| G | Typing works but the save is rejected / silently dropped | text goes in, does not survive |

One question separates them, and it is the next thing to ask rather than guess:
**in that same edit form, can the FIELDS (status, assignee, …) be changed?**

### Superseded — the original four candidates

The reporter runs one snippet on the affected item's page; it reads four values and
changes nothing:

```js
(async () => {
  const me = await (await fetch('/api/me')).json();
  const id = location.pathname.split('/').pop();
  const it = await (await fetch(`/api/pm-item/${id}`)).json();
  console.log({ me,
                created_by: it.revision_info?.created_by,
                visibility: it.data?.permission?.visibility ?? '(absent)',
                permission: it.data?.permission });
})()
```

(The resource route is `manifest.resource_route`; if `pm-item` 404s, read the right one
from `GET /api/apps/pm`.)

Outcome decides Phase 2:

- **B or C** → the owner bypass is broken. Fix the lookup, and add a test that a record's
  own creator can type in the body — asserted through the rendered editor, not by calling
  `canWriteItem` directly, or it will pass while the real path stays broken.
- **A or D** → the permission answer is right and the DEFECT IS THE SILENCE. A `readOnly`
  Monaco with no explanation is indistinguishable from a broken page; the reporter's own
  words were "I can't edit", not "I am not allowed to edit". Show who may grant access and
  what to ask for, in the same shape as the platform's other refusals.

### Phase 3 — the other bodies

Every body editor is the same component, so they break and heal together. What differs is
the gate each container passes in, and that is what needs checking one by one:

- `AiYamlRenderer` — the board / table / gantt views
- `RecordFileRenderer` — a record opened as a file
- `EntityRecordModal` — the double-click modal (#680)

Separately, a **work item's own `description`** is NOT this path: it rides the item PATCH,
checked against `write_meta` alone, which is strictly narrower than `canWrite`. A user who
passes the entity gate can still be refused there. The env-var panel already fell through
exactly this gap, so it gets its own check rather than an assumption.

## Phase 2 — RESOLVED: the cause was `<label>`, and permissions were never involved

The remaining candidates were E (the body editor fails to mount), F (a different surface
than assumed) and G (the save is dropped). It is **E, with a cause worth naming precisely**:
the body's Monaco was wrapped in a `<label>`.

A `<label>` promises to hand focus to *its native control* when clicked. Monaco is not a
native control — it is divs plus a hidden textarea — so the wrapper intercepted the click
and the caret never landed. The frontmatter fields above it, which ARE native inputs, kept
working, which is exactly the reported shape ("the fields are fine, the body is not").

It was the only one of its kind. The raw-YAML Monaco twenty lines up in the same file uses
a `<div>` with the same class, and the six other `MonacoEditor` call sites use no wrapper
at all. Nothing is lost by dropping it — Monaco carries its own `ariaLabel`.

**Why every test stayed green**: this suite MOCKS Monaco as a plain textarea, and a
textarea inside a label behaves perfectly. The defect was structurally unreachable from the
test that covers this component, so the guard added with the fix is structural too
(`closest("label")` must be null), and it is mutation-verified — restoring the `<label>`
turns exactly that test red.

The disclosure work this phase would have needed under candidates A/D is **not** needed:
nobody was refused anything.

## Phase 3 — DONE: no second instance

Scanned every `<label>` in `web/src` for a non-native control inside it.

- `EntityFileEditor:125` wraps `RoleField`, and every widget it can render is native:
  `select` / `actor` / `ref` are `<select>`, date and the rest are `<input>`, and
  `readonly` is a non-interactive `<span>`. No defect.
- Every other `<label>` in the codebase wraps a native `<input>` (checkboxes, radios).
- The six other `MonacoEditor` call sites wrap it in nothing.

**One edge case, recorded and deliberately not fixed**: the `daterange` widget renders TWO
`<input type=date>` under a single `<label>`, so clicking the label focuses only the first.
Pre-existing, cosmetic, and not the reported symptom — folding it into this fix would mix an
unrelated change into a branch whose point is one precise cause.

The work item's own `description` was also checked and is a separate path (item PATCH,
gated on `write_meta`, narrower than `canWrite`). It is unaffected by any of this, because
none of this turned out to be about permissions.

## Report 2 — the board card should show `#33`

Independent of the above and much smaller. `BoardView.tsx:237` today:

```tsx
{fieldText(entity.fields[titleField]) || `#${entity.number}`}
```

The number is a **fallback for a missing title**, so a card that has a title never shows
its number — and the number is what a person says out loud ("look at 33"). Wanted: the
number always present, muted, ahead of the title.

### Phase 4 — show it

Render the number as its own muted span before the title, keep the existing fallback for
an untitled record (without printing the number twice). Two things to get right rather
than assume:

- **Contrast**: muted still has to clear the project's graded contrast bar. Verify in BOTH
  themes — dark hides light's palette defects, which is how #690 shipped unreadable text
  with unit tests green.
- The card is a drag handle and double-click opens the record; the number must not become
  a click target that eats either gesture.

## Order

Phase 4 first — it depends on nothing and is visible immediately. Phase 1 needs the
reporter, and Phases 2 and 3 cannot honestly be written until it answers.
