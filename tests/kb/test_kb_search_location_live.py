"""Issue #263 live canned check — a real small model must FILL the location
params from the kb_search docstring (fake-LLM tests prove the plumbing, not that
the prompt actually elicits the right tool call). Marked integration: runs in the
full local suite against Ollama, not CI. "Replay" shape — one context→LLM call,
no tool execution.
"""

from __future__ import annotations

import json

import httpx
import pytest

pytestmark = pytest.mark.integration

_MODEL = "ollama_chat/qwen3:14b"
_BASE = "http://localhost:11434"


def _model_available() -> bool:
    try:
        tags = httpx.get(f"{_BASE}/api/tags", timeout=3).json()
    except Exception:
        return False
    return any(m.get("name") == "qwen3:14b" for m in tags.get("models", []))


@pytest.mark.skipif(not _model_available(), reason="qwen3:14b not pulled in local Ollama")
def test_small_model_fills_page_range_params():
    import litellm

    from workspace_app.agent.tools import build_tools

    tool = next(t for t in build_tools(["kb_search"]) if t.name == "kb_search")
    tools = [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.params_json_schema,
            },
        }
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "You are a KB assistant. Use the kb_search tool to find "
                "information. The knowledge base contains a file named manual.pdf."
            ),
        },
        {
            "role": "user",
            "content": "請告訴我為什麼 reflow 會失敗，根據 manual.pdf 的第 3 到第 5 頁。",
        },
    ]
    resp = litellm.completion(
        model=_MODEL,
        base_url=_BASE,
        messages=messages,
        tools=tools,
        tool_choice="auto",
        num_retries=0,
        timeout=120,
    )
    calls = resp.choices[0].message.tool_calls or []
    assert calls, "model should call kb_search for a located question"
    call = calls[0]
    assert call.function.name == "kb_search"
    args = json.loads(call.function.arguments)
    # The location intent ("manual.pdf, pages 3-5") must reach the tool args —
    # the docstring has to teach the small model to fill document + page range.
    assert args.get("document") == "manual.pdf"
    assert args.get("page_from") == 3
    assert args.get("page_to") == 5
