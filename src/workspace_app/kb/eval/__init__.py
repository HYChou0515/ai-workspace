"""Retrieval-quality eval (#535).

An offline, zero-domain-knowledge baseline for the CURRENT retriever: generate a
synthetic question from each sampled chunk (Promptagator — Dai et al., 2022),
run it through the real retriever, and measure whether the source chunk comes
back near the top (recall@k / MRR). The corpus IS the label — no human queries,
no gold answers. This is the prerequisite gate for #533 / #534: it says whether
any later change actually helped.

``score`` is the pure metric core (this phase). Later phases add synthetic-Q
generation, deterministic sampling, the ``Graph``-style fan-out job, and the
``EvalResult`` resource.
"""
