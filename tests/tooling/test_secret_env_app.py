"""A name `env.json` marks secret is the tool's own credential, so the app never
treats it as something a person fills in (docs/plan-tool-identity-secrets.md P4).

The user decided: "這組帳密是工具這一方的，不該讓使用者知道，也不該要使用者去填".
So it is not a field in the environment panel, and the card that asks a user to
set a variable refuses it — even when the tool printed it while failing.
"""

from __future__ import annotations

from dataclasses import replace

from agents import RunContextWrapper

from tests.agent.test_request_env import _ctx, _pkg
from workspace_app.agent.env_request import ENV_REQUEST_MARKER, request_env_impl
from workspace_app.tooling.external import _package
from workspace_app.tooling.registry import EnvNeed

_DESCRIBED = {
    "commands": [{"name": "lot-status", "description": "d", "params_json_schema": {}}],
    "env": [
        {"name": "MACHINE_PASSWORD", "description": "", "required": None, "secret": True},
        {"name": "ERP_BASE", "description": "site", "required": False},
    ],
}


def test_a_secret_name_is_not_a_field_anyone_fills_in():
    pkg = _package("erp", _DESCRIBED)
    assert pkg.env_needs == (EnvNeed(name="ERP_BASE", description="site", required=False),)
    assert pkg.secret_env == ("MACHINE_PASSWORD",)


def test_a_tool_with_only_secret_names_still_says_it_declared_something():
    """`()` is "the author looked, nothing for you to set" — not `None`, which
    is "nobody said", and which the panel words differently."""
    described = {**_DESCRIBED, "env": [_DESCRIBED["env"][0]]}
    assert _package("erp", described).env_needs == ()


async def test_the_card_refuses_a_secret_name_even_when_the_tool_printed_it():
    pkg = replace(_pkg("erp", "lot-status", third_party=True), secret_env=("MACHINE_PASSWORD",))
    actx = _ctx(pkg, outputs={"erp__lot-status": "error: MACHINE_PASSWORD is not set"})
    reply = await request_env_impl(
        RunContextWrapper(actx),
        tool="erp__lot-status",
        names=["MACHINE_PASSWORD"],
        reason="needs a password",
    )
    assert ENV_REQUEST_MARKER not in reply
    assert "cannot set" in reply
    assert "platform team" in reply
