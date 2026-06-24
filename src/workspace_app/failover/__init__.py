"""Busy-aware LLM failover (#196 + #131).

A single strict-priority failover loop shared by every role that calls a model
(KB retrieval LLM, VLM, agent / sub-agent chat, embedder). When the front model
is too busy — whether it returns a fast error or just stalls before the first
token — the loop switches to the next model in the chain and parks the busy one
on a short cooldown so subsequent requests skip it.

The policy lives in :mod:`workspace_app.failover.core`; thin per-interface
adapters (`FallbackLlm` / `FallbackVlm` / …) sit behind the existing `ILlm` /
`IVlm` / SDK `Model` seams so KB and app share one brain.
"""
