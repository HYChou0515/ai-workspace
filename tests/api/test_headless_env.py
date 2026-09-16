"""A turn with NO request behind it gets what the deploy's ``IRequestEnv`` says
it gets (``docs/plan-headless-env.md``).

#714 wired the seam only where a request exists — a chat send, a WUI
``callTool`` — and left every request-less turn (a scheduled workflow node, a
goal-driver continuation, an event-triggered run) with the item's ``env_vars``
alone. That made the item's panel the only home for a service-account
credential: one hand-copied, expiring copy per item.

The seam now has a second method, ``env_without_request``, asked by exactly
those turns with the user the run was captured as. The platform still learns no
word for "service account": what comes back is the impl's policy.
"""

from __future__ import annotations

from fastapi import Request

from workspace_app.api.request_env import IRequestEnv


class OnlyEnvFor(IRequestEnv):
    """A deploy written against #714's interface: ``env_for`` and nothing else."""

    async def env_for(self, request: Request, *, user_id: str, item_id: str) -> dict[str, str]:
        return {"CALLER": user_id}


async def test_an_impl_written_before_this_method_existed_still_works_and_gives_nothing():
    """The default is today's behaviour, so a deploy that never heard of headless
    turns is neither broken (the class must still instantiate) nor surprised
    (its scheduled turns still carry nothing)."""
    seam = OnlyEnvFor()

    assert await seam.env_without_request(user_id="alice", item_id="item-1") == {}
