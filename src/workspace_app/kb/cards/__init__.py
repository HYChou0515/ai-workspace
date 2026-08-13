"""Context cards built the way the knowledge graph is: evidence, then derivation.

Deliberately SEPARATE from ``kb.graph``, which extracts overlapping evidence from
the same documents. Merging them would save a model pass — and would make the two
approaches share a failure mode at the layer most likely to have one, which is
exactly the comparison the owner is currently running. The duplication is the
experiment; see ``docs/plan-context-card-evidence.md``.

Also separate from the live ``kb.card_gen`` pipeline: nothing here opens a store,
so a criterion can be tried against real documents offline without writing
anything anywhere.
"""
