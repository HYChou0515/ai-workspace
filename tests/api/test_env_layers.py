"""Viewer login (`docs/plan-wui-viewer-login.md`) — which layer a tool's variable comes from.

Two layers: the item's ``env_vars`` (SHARED — one copy every participant can
read) and a PRIVATE one belonging to the person the tool runs for. Each name on
the item carries a policy that picks between them; a name with no policy is
``shared_first``, which is exactly the merge every turn did before this plan.
"""

from __future__ import annotations

import itertools

import pytest

from workspace_app.api.env_layers import resolve_env

S, P = {"K": "shared"}, {"K": "private"}


@pytest.mark.parametrize(
    ("policy", "shared", "private", "expected"),
    [
        # shared_first — the shared copy wins; the private one fills a gap.
        ("shared_first", S, P, "shared"),
        ("shared_first", {}, P, "private"),
        ("shared_first", S, {}, "shared"),
        # private_first — the person's own value wins; the shared one is a fallback.
        ("private_first", S, P, "private"),
        ("private_first", S, {}, "shared"),
        ("private_first", {}, P, "private"),
        # private_only — the shared copy is never used, even as a fallback.
        ("private_only", S, P, "private"),
        ("private_only", {}, P, "private"),
    ],
)
def test_each_policy_picks_its_layer(policy, shared, private, expected):
    assert resolve_env(shared=shared, private=private, policy={"K": policy})["K"] == expected


@pytest.mark.parametrize("policy", ["shared_first", "private_first", "private_only"])
def test_a_name_neither_layer_holds_is_not_passed_at_all(policy):
    """A policy is not a requirement: a missing value leaves the name out, the
    same as a name nobody ever set. Whether the tool can live without it is the
    tool's business (`env.json`'s `required` is a hint, #750)."""
    assert resolve_env(shared={}, private={}, policy={"K": policy}) == {}


def test_private_only_ignores_a_shared_value_even_when_the_person_has_none():
    assert resolve_env(shared=S, private={}, policy={"K": "private_only"}) == {}


def test_no_policy_is_the_merge_every_turn_did_before():
    """Parity with the old line `{**private, **shared}` as the oracle, over every
    way three names can sit in two layers — not a hand-picked case."""
    names = ("A", "B", "C")
    for placement in itertools.product(("none", "shared", "private", "both"), repeat=3):
        where = dict(zip(names, placement, strict=True))
        shared = {n: f"s{n}" for n, w in where.items() if w in ("shared", "both")}
        private = {n: f"p{n}" for n, w in where.items() if w in ("private", "both")}
        # ITEMS, not the dict: `_tool_env` joins the names into
        # `SANDBOX_USER_ENV_KEYS`, so the order is part of what a tool sees.
        got = resolve_env(shared=shared, private=private, policy={})
        assert list(got.items()) == list({**private, **shared}.items())


def test_an_unknown_policy_value_reads_as_the_default():
    """The item is a plain PATCH-able record; a stray string must not become a
    third behaviour nobody wrote. It resolves like no policy at all."""
    assert resolve_env(shared=S, private=P, policy={"K": "bogus"}) == {"K": "shared"}


# ─── the turn: an item's policy reaches the tools a turn dispatches ─────────


async def test_a_chat_turn_hands_its_tools_the_layer_the_items_policy_names():
    from .test_request_env import _app_with_item, _chat_turn

    builder, item_id = _app_with_item(
        {"TOKEN": "shared", "HOST": "db"}, env_policy={"TOKEN": "private_first"}
    )

    ctx = await _chat_turn(builder, item_id, caller_env={"TOKEN": "mine", "HOST": "mine"})

    assert ctx.user_env == {"TOKEN": "mine", "HOST": "db"}


async def test_a_workflow_turn_honours_the_policy_too():
    from .test_request_env import _app_with_item, _dummy_subagent

    builder, item_id = _app_with_item({"TOKEN": "shared"}, env_policy={"TOKEN": "private_only"})

    ctx = await builder.build_workflow_turn(
        item_id,
        agent_config=None,
        run_subagent=_dummy_subagent,
        history_messages=[],
        caller_env={},
    )

    assert ctx.user_env == {}
