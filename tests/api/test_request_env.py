"""#714 — environment variables derived from the REQUEST that triggered a turn.

The item's ``env_vars`` (#673) are one shared copy: stored on the item, plainly
readable by every participant, identical no matter who pressed send. This seam
carries the other kind — what the *person pressing send* is carrying on their
request (an SSO session cookie, a gateway header) — which is per-requester and
therefore may never be written back to the item.

The platform knows no cookie name. A deploy plugs in its own ``IRequestEnv``
through ``server.request_env``, the same dotted-path convention ``kb.parsers``
and ``health.checks`` use.
"""

from __future__ import annotations

import pytest
from fastapi import Request

from workspace_app.api.request_env import IRequestEnv
from workspace_app.factories import get_request_env


class StubRequestEnv(IRequestEnv):
    """Module-level so the dotted-path resolver can import it."""

    async def env_for(self, request: Request, *, user_id: str, item_id: str) -> dict[str, str]:
        return {"CALLER": user_id}


def test_unconfigured_deploy_has_no_request_env_source():
    """No dotted path ⇒ the behaviour does not exist at all, and a turn's env
    keeps coming from the item alone."""
    assert get_request_env("") is None


def test_configured_dotted_path_is_resolved_to_an_instance():
    source = get_request_env("tests.api.test_request_env.StubRequestEnv")
    assert isinstance(source, StubRequestEnv)


def test_a_class_that_is_not_a_request_env_is_refused_at_startup():
    """Loud at boot, not at the first send: a deploy that mis-names its impl
    should never reach the point where a turn silently runs without the
    caller's identity."""
    with pytest.raises(TypeError, match="not an IRequestEnv subclass"):
        get_request_env("workspace_app.config.schema.Settings")
