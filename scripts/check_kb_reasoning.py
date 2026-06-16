#!/usr/bin/env python
"""Live check: does ``kb.retrieval_llm.reasoning_effort`` actually control each
model's thinking on the kb_search retrieval path?

This drives the SAME code path kb_search's multi-query / HyDE / rerank use
(``LitellmLlm.stream``), once per (model, effort), and reports whether the model
emitted reasoning (``<think>``) chunks. It does NOT touch the app, a collection,
or the index — it's a focused probe of the model + the litellm→Ollama ``think``
mapping, for the mixed Ollama fleet (qwen3.5 / qwen3.6 / glm5.1 / qwen3:14b …).

Expectation per model that supports thinking:

    reasoning_effort=None  -> THINKS   (param omitted → model default)
    reasoning_effort=none  -> no-think (litellm maps to Ollama think=False)
    reasoning_effort=low   -> THINKS   (think=True)

If a model shows THINKS for ``none``, its Ollama packaging doesn't honour
``think=False`` and it needs a model-specific disable instead — report it.

Usage (needs a reachable Ollama; set --base-url or OLLAMA_API_BASE):
    uv run python scripts/check_kb_reasoning.py ollama_chat/qwen3:14b \\
        ollama_chat/qwen3.6 ollama_chat/glm5.1 --base-url http://localhost:11434
"""

from __future__ import annotations

import argparse

from workspace_app.kb.llm import LitellmLlm

# A multi-query-shaped prompt — the kind kb_search's expansion step sends.
_PROMPT = (
    "Give exactly 3 alternative search queries for: reflow oven void rate. "
    "Output only the queries, one per line."
)

_EFFORTS: tuple[str | None, ...] = (None, "none", "low")


def _probe(model: str, base_url: str | None, effort: str | None) -> tuple[bool, int]:
    """Return (did_the_model_emit_reasoning, answer_char_count) for one run."""
    llm = LitellmLlm(model, base_url=base_url, reasoning_effort=effort)
    reasoned = False
    answer_chars = 0
    for text, is_reasoning in llm.stream(_PROMPT):
        if is_reasoning:
            reasoned = True
        else:
            answer_chars += len(text)
    return reasoned, answer_chars


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("models", nargs="+", help="e.g. ollama_chat/qwen3:14b ollama_chat/glm5.1")
    ap.add_argument("--base-url", default=None, help="Ollama base url (else OLLAMA_API_BASE)")
    args = ap.parse_args()

    for model in args.models:
        print(f"\n== {model} ==")
        for effort in _EFFORTS:
            label = "(omit)" if effort is None else effort
            try:
                reasoned, chars = _probe(model, args.base_url, effort)
                print(
                    f"  reasoning_effort={label:<6} -> {'THINKS' if reasoned else 'no-think'} "
                    f"({chars} answer chars)"
                )
            except Exception as exc:  # noqa: BLE001 — a live probe: report and keep going
                print(f"  reasoning_effort={label:<6} -> ERROR: {type(exc).__name__}: {exc}")
    print("\nWant: none -> no-think; low / (omit) -> THINKS. A 'THINKS' on none means")
    print("that model ignores think=False and needs a model-specific disable.")


if __name__ == "__main__":
    main()
