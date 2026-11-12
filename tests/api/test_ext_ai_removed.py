"""「問外部AI」(`ask_ext_ai`) is gone (docs/plan-remove-ext-ai.md, R1): as if it
never existed — no tool, no switch, no card, no helper errand. What it left in
stored data stays readable (R4, R5), and an old helper still checks in.
"""

from __future__ import annotations

import re
from pathlib import Path

import msgspec
import pytest

from tests.api.test_outside_answers import _asked
from tests.api.test_tool_grant_doors import _agent_for, _build, _chat_ctx
from workspace_app.agent.outside_lookup import OUTSIDE_LOOKUP_MARKER, declared_lookup
from workspace_app.config.loader import load


def test_the_switch_is_gone_and_a_config_that_still_sets_it_refuses_to_boot(tmp_path: Path):
    """R1: the loader rejects keys it does not know — the operator deletes the
    line before rolling out (the migrations entry), named here so they can."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  ext_ai_enabled: false\n", encoding="utf-8")

    with pytest.raises(ValueError, match=re.escape("unknown key 'server.ext_ai_enabled'")):
        load(config_path=cfg, env={})


async def test_no_chat_turn_is_offered_ask_ext_ai(monkeypatch):
    """The live path: a chat turn built by the app, through the runner's own
    kwargs into `_agent_for`. No `ext_ai` rides along any more."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner
    from workspace_app.tokens import LlmCredential

    spec, builder, _, _, iid = _build(monkeypatch, {})
    ctx = await _chat_ctx(spec, builder, iid)
    kw = LitellmAgentRunner()._agent_kwargs(ctx, LlmCredential)
    agent = _agent_for(ctx.agent_config, ctx.packages, ctx.unavailable_tools, **kw)

    assert "ext_ai" not in kw
    names = {t.name for t in agent.tools}
    assert "ask_outside" in names
    assert "ask_ext_ai" not in names


def test_a_question_card_is_no_card():
    """The `{why, question}` card was the 問外部AI card; nothing declares one
    now, so a reply carrying one stops nothing (R5)."""
    m = OUTSIDE_LOOKUP_MARKER
    assert declared_lookup(f'x{m}{{"why":"w","question":"q"}}') is None


def test_an_old_helpers_check_in_with_ext_ai_is_still_accepted():
    """§4: a helper packed before this still reports `ext_ai`; the field is
    ignored, the check-in is not refused — no protocol bump."""
    client, _spec, _iid = _asked()

    r = client.post(
        "/ai-workspace-helper/heartbeat",
        json={"version": "0.1.0", "protocol": 2, "ext_ai": "company-ai"},
    )

    assert r.status_code == 204, r.text
    assert client.get("/ai-workspace-helper/status").json() == {"online": True}


def test_a_stored_ask_ext_ai_job_still_reads():
    """R4: a job row written as `ask_ext_ai` before this decodes — the job
    list and the card's read never 500 on it."""
    from workspace_app.api.outside_jobs import OutsideJob

    row = {
        "owner": "u",
        "item_id": "i",
        "slug": "s",
        "chat_id": "c",
        "tool_call_id": "t",
        "kind": "ask_ext_ai",
        "args": "a question",
        "state": "done",
        "created_ms": 1,
    }
    job = msgspec.convert(row, OutsideJob)

    assert job.kind == "ask_ext_ai"


def test_a_stored_presence_row_with_ext_ai_still_reads():
    """An old helper's check-in row carries `ext_ai`; the field is gone from
    the model, and the row must still decode (the status read never 500s)."""
    from workspace_app.api.outside_jobs import _AiWorkspaceHelperPresence

    row = {"owner": "u", "last_seen_ms": 1, "protocol": 2, "ext_ai": "company-ai"}

    seen = msgspec.convert(row, _AiWorkspaceHelperPresence)

    assert (seen.owner, seen.protocol) == ("u", 2)
