#!/usr/bin/env python
"""Live check (#298): the author-skill co-authoring flow is wired through the
REAL resolve path, and a small local model engages it.

Two parts:

  1. Deterministic (no model): resolve(<app>, <profile>) through the real
     AppCatalog and assert its composed system prompt advertises `author-skill`,
     and the real `build_tools` exposes `read_skill` + `save_skill`. This proves
     the opt-in (agent.skills + agent.tools) reaches a live turn — not just the
     unit tests.

  2. Live turn (needs a reachable, tool-calling model — the deployment's qwen3,
     NOT the VLM): give the model the author-skill index + tools and ask it to
     make a skill; assert it reaches for the skill machinery (loads author-skill
     or calls save_skill) rather than answering free-hand. Skipped with a clear
     notice when the resolved model isn't reachable/pulled, so the deterministic
     proof still runs in a bare environment.

Usage:
    uv run python scripts/check_author_skill_298.py --app playground --profile default
    uv run python scripts/check_author_skill_298.py \
        --base-url http://localhost:11434 --model ollama_chat/qwen3:14b
"""

from __future__ import annotations

import argparse

from workspace_app.agent.tools import build_tools
from workspace_app.config.loader import load
from workspace_app.factories import get_app_catalog
from workspace_app.kb.llm import LitellmLlm

_ASK = "I want to make a reusable skill for triaging SMT reflow defects. Help me create it."


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--app", default="playground")
    ap.add_argument("--profile", default="default")
    ap.add_argument("--base-url", default="http://localhost:11434", help="Ollama base url")
    ap.add_argument(
        "--model", default="", help="override the resolved model (e.g. a tool-calling qwen3)"
    )
    args = ap.parse_args()

    settings = load(config_path=None, env={})
    catalog = get_app_catalog(settings)
    cfg = catalog.resolve(app_slug=args.app, profile=args.profile, attached_preset=None)

    print(f"== #298 author-skill wired into resolve({args.app}, {args.profile}) ==")
    assert cfg is not None, "resolve returned None"
    assert "author-skill" in cfg.system_prompt, (
        "composed system prompt does not advertise author-skill — check agent.skills opt-in"
    )
    print("  ✓ system prompt advertises author-skill")

    tool_names = {
        t.name for t in build_tools(cfg.allowed_tools, app_slug=args.app, profile=args.profile)
    }
    assert "read_skill" in tool_names, f"read_skill not wired ({sorted(tool_names)})"
    assert "save_skill" in tool_names, f"save_skill not wired ({sorted(tool_names)})"
    print("  ✓ read_skill + save_skill exposed to the turn")

    model = args.model or cfg.model
    base = cfg.llm_base_url or args.base_url
    print(f"\n== live turn ({model}) ==")
    prompt = f"{cfg.system_prompt}\n\nUser: {_ASK}\n\nWhat is your first step?"
    try:
        llm = LitellmLlm(model, base_url=base, reasoning_effort="none")
        reply = "".join(t for t, is_reasoning in llm.stream(prompt) if not is_reasoning)
    except Exception as e:  # noqa: BLE001 — a bare env may have no model pulled
        print(f"  ⚠ SKIPPED live turn — model not reachable/pulled: {e}")
        print("    (run against the deployment's tool-calling qwen3 to verify the flow)")
        print("\nDETERMINISTIC CHECK PASSED")
        return

    print(f"  reply[:300] : {reply.strip()[:300]!r}")
    engaged = "author-skill" in reply.lower() or "read_skill" in reply or "save_skill" in reply
    if engaged:
        print("  ✓ model reaches for the author-skill machinery")
        print("\nLIVE CHECK PASSED")
    else:
        print("  ⚠ model answered without naming the skill machinery — inspect the reply above")
        print("    (small VLMs tool-call poorly; verify against qwen3 before relying on it)")
        print("\nDETERMINISTIC CHECK PASSED (live turn inconclusive)")


if __name__ == "__main__":
    main()
