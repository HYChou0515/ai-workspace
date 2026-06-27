# Plan — #280 RCA can change collection_ids (tiered, web-editable, per-profile default)

Grill-locked design (see conversation). Two orthogonal capabilities, both built as
**platform-generic** mechanisms (every App is a template — no RCA-specific FE):

1. **Configurable collection scope with a per-profile default + per-item web override.**
2. **Priority-tiered KB retrieval with agent-driven fallback** across all three
   per-collection retrieval modes (glossary / doc-chunk / wiki).

## Data model

`collections.json` (per-item workspace file, hand-editable + picker-written) gains an
optional `tier` int per entry — **backward compatible** (no `tier` ⇒ tier 0):

```json
[
  { "id": "c1", "name": "A", "tier": 0 },
  { "id": "c2", "name": "B", "tier": 0 },
  { "id": "c3", "name": "D", "tier": 10 }
]
```

- Entries grouped by `tier`; distinct tier values sorted ascending ⇒ **ranks** 0,1,2…
- Tier ints are **sparse** (0,10,20) so operators can insert between later. The agent
  never sees raw tier ints — it addresses tiers by **rank** (ordinal). `rank` out of
  range ⇒ "no more tiers", agent stops.

## Retrieval / fallback (via `ask_knowledge_base`, platform-level)

- `ask_knowledge_base(question, rank=0)`: scopes the kb sub-agent to **rank**'s
  collection subset. All three modes (glossary / chunks / wiki) read `ctx.collection_ids`,
  so scoping that to the rank's subset tiers **all three at once** — no per-mode rank.
- The **consumer agent (RCA) judges + escalates**: reads rank 0's answer; if not enough,
  calls rank 1; each rank's answer stays in its context, so it can **compare + revert**.
  The kb sub-agent does NOT pre-judge "found / not" (else the consumer is blind to
  sub-optimal-but-useful results).
- Empty / no `collections.json` ⇒ single implicit tier = **search all collections**
  (purely additive; today's behaviour preserved).
- Tool output reports "Searched priority tier R of N … widen with rank=R+1" and, past the
  last tier, "no priority tier R; the last is R-1".
- `infer_modules` unchanged; the direct KB-chat page is out of v1 scope.

## Per-profile default

`_profile.json` gains optional `collections` declared by **name** + `tier`:

```jsonc
"collections": [
  { "name": "fab-process-docs", "tier": 0 },
  { "name": "archive-2019", "tier": 10 }
]
```

At item creation, resolve each `name` → live collection id and write `collections.json`
(`[{id,name,tier}]`). Names that resolve to no collection are **skipped + logged** (item
creation never hard-fails on a stale default). Generic to every App's profile.

## Frontend (platform-generic, in the shared picker)

- `CollectionsPickerModal` gains a tier editor: selected collections organised into
  **ordered priority groups** (group order = rank; stored as sparse tier ints).
  Backward compatible: a flat file = one group.
- RCA participates via the generic path: `app.json` agent `context_files: ["collections.json"]`
  ⇒ `showCollections` derives true ⇒ picker appears. No RCA-specific FE.

## Phases (flat)

- **P1** Backend parser: `collection_tiers_from_json` (group→sorted ranks; tolerant). Keep `collection_ids_from_json` (flat union).
- **P2** `AgentToolContext.collection_tiers` + `ask_knowledge_base(rank)` + bridge scope threading + tier banner + empty=all + out-of-range message.
- **P3** `_hub_collection_tiers` wired into the turn (`ctx.collection_tiers` set for RCA/topic-hub).
- **P4** Profile default: `_profile.json.collections` (name+tier) → resolve + seed `collections.json` (skip unresolvable + log).
- **P5** RCA `app.json` `context_files` + RCA prompt escalation/compare guidance.
- **P6** FE `collectionsFile.ts`: parse/serialize `tier`.
- **P7** FE `CollectionsPickerModal`: ordered priority-group editor.
- **P8** Full gate (coverage 100% / ruff / ty / FE typecheck+build+vitest), commit, PR, CI, merge.

DoD: live check that a small local model (qwen3) actually escalates rank + compares
(per "LLM features need live checks").
