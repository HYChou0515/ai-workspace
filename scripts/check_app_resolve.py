#!/usr/bin/env python
"""Live check (#89 P8): a new per-App RCA item's turn resolves the right
AgentConfig through the AppCatalog (app ◇ profile ◇ preset) — the path that
REPLACED the removed workspace_chat picker / legacy resolve — and that resolved
model actually responds live.

It drives the real `factories.get_app_catalog` + the real `LitellmLlm.stream`
(the same code a live turn's LLM call uses), NO tools / sandbox / index. Two
assertions:

  1. resolve(rca, default) -> model is the rca default preset's model and the
     system prompt carries the RCA-agent contract (base prompt composed in).
  2. that model answers a tiny RCA-flavoured prompt live (non-empty reply).

Usage (needs a reachable Ollama):
    uv run python scripts/check_app_resolve.py --base-url http://localhost:11434
"""

from __future__ import annotations

import argparse

from workspace_app.config.loader import load
from workspace_app.factories import get_app_catalog
from workspace_app.kb.llm import LitellmLlm

_PROMPT = "In one sentence, what is the first step of a root-cause analysis?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:11434", help="Ollama base url")
    args = ap.parse_args()

    settings = load(config_path=None, env={})  # bundled defaults: rca default → qwen3-local
    catalog = get_app_catalog(settings)
    cfg = catalog.resolve(app_slug="rca", profile="default", attached_preset=None)

    print("== #89 AppCatalog resolve(rca, default) ==")
    assert cfg is not None, "resolve returned None — AppCatalog didn't resolve the rca App"
    print(f"  model           : {cfg.model}")
    print(f"  allowed_tools    : {cfg.allowed_tools}")
    print(f"  prompt[:80]      : {cfg.system_prompt[:80]!r}")
    assert cfg.model == "ollama_chat/qwen3:14b", f"unexpected model: {cfg.model}"
    assert "RCA" in cfg.system_prompt or "root cause" in cfg.system_prompt.lower(), (
        "resolved system prompt is missing the RCA-agent contract"
    )
    print("  ✓ resolved to the rca default preset + RCA base prompt")

    print("\n== live turn (LitellmLlm.stream, no tools) ==")
    base = cfg.llm_base_url or args.base_url
    llm = LitellmLlm(cfg.model, base_url=base, reasoning_effort="none")
    answer = "".join(text for text, is_reasoning in llm.stream(_PROMPT) if not is_reasoning)
    print(f"  reply[:200]      : {answer.strip()[:200]!r}")
    assert answer.strip(), "live model returned an empty reply"
    print("  ✓ resolved model answered live")
    print("\nLIVE CHECK PASSED")


if __name__ == "__main__":
    main()
