---
name: skill-hub
description: Share a skill with everyone, or find one somebody else published — the skill hub. Use when the user wants to publish / share / upload a skill, asks whether a skill for some task already exists, or wants to install one another person made.
---

# The skill hub

The **skill hub** is where people publish the skills they made in their own
items, so others can install them into theirs. Three tools do the work —
`search_skill_hub`, `install_skill`, `publish_skill` — and this skill is about
*when* to reach for each and *what to tell the user* around it. Write in the
user's language.

## Finding one: `search_skill_hub`

When the user asks "is there a skill for…", "has anyone made…", or wants to
install one, search first. Every hit shows `owner/name`, a description, which
App it was written in, and its **entry id** — the id is what `install_skill`
takes, so keep it. Forks are listed under the skill they were forked from.

A hit may say it *mentions tools this App lacks*. That is not a refusal —
**tell the user before installing**, in one sentence: which tools, and that
the steps needing them may not be followable in this item. Then let them
decide. Never install a skill the user has not picked.

## Installing one: `install_skill(entry_id)`

Install into **this** item. The copy lands in `.skill/<name>/` and is in the
skill index from the next turn on; you can `read_skill('<name>')` right away.

Two refusals to relay plainly:

- **A folder of that name is already here.** The reply names whose copy it is.
  Ask the user whether to remove or rename theirs first — never do it for them.
- **No such entry.** The id is wrong, or the entry was taken down. Search again
  rather than guessing an id.

After installing, if the publish-time review left notes, pass them on: they
are the publisher's own reviewer telling the next person what to watch for.

## Publishing one: `publish_skill(name)`

Only when the user has said which skill, and said to publish it. `name` is the
folder under `.skill/` — a skill you saved with `save_skill`, wrote by hand
together, or installed from the hub and changed.

Before calling it, say what publishing means, once, briefly: the skill becomes
visible to **everyone on the platform** (public by default; the owner can
narrow that on the skill hub page), and re-publishing the same name replaces
their earlier version.

What comes back, and what to do with it:

- **An `error:` naming structural problems** (frontmatter name ≠ folder, no
  description, a body over the cap, a `references/` file the body names but
  does not ship, a script that does not parse). Nothing was published. Fix
  them **with** the user — usually a `save_skill` or an edit — and publish
  again. Do not paraphrase the list; it is exact.
- **Published, with reviewer notes.** It IS published. Relay the notes
  verbatim as suggestions, and offer to act on any the user wants — then
  publish again to replace the version.
- **Published, nothing flagged.** Say so, and where it now lives: the skill
  hub page, under `owner/name`.
- **"The review service could not review it."** Nothing was published, and
  it is not the skill's fault. Ask the user to try again later.

A skill installed from **someone else's** entry and published from here
becomes a **fork** of theirs — say so when the reply does, so the user knows
the original is still the original.

## What NOT to do

- Do not publish on the strength of "save this skill" — saving is
  `save_skill`; publishing is a separate, explicit ask.
- Do not install into an item the user did not mean; `install_skill` only ever
  targets this one.
- Do not describe the skill hub's rules from memory. The tool replies carry
  the truth for this deploy; relay them.
