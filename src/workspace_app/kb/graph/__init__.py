"""Knowledge graph over the KB (#534).

Slice 1 — metric tracking: pull metric claims (metric / period / value / unit)
out of the VLM-markdown of each chunk, store them in a flat, queryable
``GraphClaim`` table, and let a metric's values be listed across every deck.

``extract`` is the pure extraction core (this phase); later phases add the
``GraphClaim`` resource, the writer, and the fan-out coordinator (mirroring the
#535 eval). Entity resolution, contradiction detection, and the full Graph*
family (GraphEntity / GraphMention / GraphRelationship / GraphSummary) are later
slices.
"""
