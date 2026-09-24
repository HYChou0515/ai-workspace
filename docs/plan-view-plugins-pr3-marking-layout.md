# Plan — PR 3: named markings and layouts

Part of [plan-view-plugins.md](plan-view-plugins.md). The decisions are Q5, Q5.1–Q5.3,
Q6, Q10 and Q17. This PR stacks on PR 2 and is #847's acceptance scene: brush in one
pane, and the linked views in other panes light up.

## Done means

- Two `chart` views on the same `marking:` sit in different panes of the workspace's
  own split layout. A lasso in one lights the matched rows in the other. A third view on
  a different marking, or none, does not react.
- The view header shows `🔗 <name> ▾`, which can re-attach or detach the view.
- The AI's `show_file(layout=…)` shows one card that opens the arrangement:
  - in the workspace by the Q17 rule;
  - in chat mode as an editor-area-only page.
- A marked set sent with a message is visible as chips, persisted, and readable by the
  AI at `.markings/<name>.json`.

## Phases

**P1 — the marking store.**

- Workspace-level state per item, living beside `useEditorGroups` in `WorkspaceShell`.
  It holds `name → {column → set of values}`.
- Exposed through the SDK as `useMarking(name)`, which reads and writes.
- It is knowledge-free: columns and values are opaque strings (Q6).
- Matching rule: a row is lit when, for every column it shares with the marking, its
  value is in the set.
- A view without `keys:` can be lit but cannot write.

**P2 — writing and reading marks in `chart`.**

- Brush, lasso and legend results are projected onto the spec's `keys` and written to
  the named marking.
- Every view on that marking re-derives its lit set.
- Highlight from the spec seeds the marking on open, resolved through `query` exactly
  as in PR 2.
- A test proves a view on another marking does not re-render on a write.

**P3 — the header control.**

- `🔗 <name> ▾` offers: attach to an existing marking, attach to a new one, or detach.
- The choice lives in view state. The spec file is **not** rewritten; a user edits the
  YAML to make it permanent.

**P4 — `show_file(layout=…)`.**

- A new optional `layout` argument with the `PaneNode` shape (`paneTree.ts`), with paths
  as leaves. The existing single `path` form is unchanged.
- Every leaf path is resolved, and every `*.ai.yaml` leaf is validated as in PR 1 P9.
  One failure declares nothing.
- The `[shown-files]` marker carries the tree, and `ShownFiles.tsx` renders one card
  for it.

**P5 — opening a layout in the workspace.**

- Q17: a single pane becomes the card's layout, with the existing tabs merged into its
  top-left leaf. An already-split tree moves left, and the card's layout opens on the
  right.
- A card file already open anywhere is moved, not duplicated.
- `paneTree.ts` gets pure functions for both cases, with unit tests for each shape
  before the shell uses them.

**P6 — the chat-mode page.**

- A new route `/a/{slug}/items/{id}/view?layout=…` (or `?path=`) renders only the
  editor area:
  - panes, views and markings;
  - no file tree, no chat.
- It uses the same component as the workspace's editor area, not a copy.
- `ShownFiles.tsx` in chat mode links here instead of the raw `fileUrl`. That also fixes
  the current behaviour where a `.ai.yaml` card opens as YAML text.

**P7 — markings to the AI.**

- On send, the non-empty markings show as chips above the composer, each with a remove
  control.
- The chips that remain are:
  - written to `.markings/<name>.json` (`{source files, column → values}`);
  - recorded on the persisted user message, so they survive a reload;
  - summarised as one prompt line each (name, count per key, path).
- A selection that is never sent writes nothing.
- The write goes through `WorkspaceFiles`, so it respects the quota. On a full
  workspace it fails the send's chip with the 507 reason, not the whole send.

**P8 — docs and runbook.**

- `show_file` doc for `layout`.
- The marking concept in the chart reference.
- A `docs/migrations.md` entry. It is needed because sending with a marking now writes
  `.markings/` files into workspaces, which is a behaviour change with no knob. The
  entry covers:
  - what appears in users' trees;
  - that it counts toward the quota;
  - the check that confirms it: send with a marking and see the file.

## Verification

- Targeted tests, the four gates, and typecheck.
- Live check in a real browser at two widths:
  - #847's scene with a grid, a scatter and a table on one marking plus one unrelated
    view;
  - a layout card opened from both one-pane and split states;
  - the same card from chat mode;
  - a send with chips, then the agent reads the file.
- Base differential on PR 2's tip, where brushing lights nothing elsewhere.
