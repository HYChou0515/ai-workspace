"""Viewer login (`docs/plan-wui-viewer-login.md`) — which layer a tool's variable comes from.

Two layers: the item's ``env_vars`` (SHARED — one copy every participant can
read) and a PRIVATE one belonging to the person the tool runs for. Each name on
the item carries a policy that picks between them; a name with no policy is
``shared_first``, which is exactly the merge every turn did before this plan.
"""

from __future__ import annotations

import itertools

import pytest

from workspace_app.api.env_layers import PersonEnv, resolve_env

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

    ctx = await _chat_turn(
        builder, item_id, caller_env=PersonEnv(own={"TOKEN": "mine", "HOST": "mine"})
    )

    assert ctx.user_env == {"TOKEN": "mine", "HOST": "db"}


async def test_a_workflow_turn_honours_the_policy_too():
    from .test_request_env import _app_with_item, _dummy_subagent

    builder, item_id = _app_with_item({"TOKEN": "shared"}, env_policy={"TOKEN": "private_only"})

    ctx = await builder.build_workflow_turn(
        item_id,
        agent_config=None,
        run_subagent=_dummy_subagent,
        history_messages=[],
        caller_env=PersonEnv(),
    )

    assert ctx.user_env == {}


def test_the_shared_case_table_still_says_what_resolve_env_does():
    """`tests/fixtures/env_layers_cases.json` is what the FE's copy of this rule
    (`web/src/lib/envLayers.ts`, which labels whose value is in use) is held
    to. This pins the table to THIS function, so the two cannot drift by hand:
    change the rule and this reddens until the table is regenerated, and then
    the FE test reddens until the FE follows."""
    import json
    from pathlib import Path

    table = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "env_layers_cases.json").read_text()
    )
    # Every combination of K absent, set or blank in shared / this item's / every item's, under no
    # policy, each of the three, and an unknown one (`plan-personal-env`).
    assert len(table["cases"]) == 3 * 3 * 3 * 5
    for case in table["cases"]:
        got = resolve_env(
            shared=case["shared"],
            private=case["private"],
            personal=case["personal"],
            policy=case["policy"],
        )
        layer = {"s": "shared", "p": "private", "v": "personal"}.get(got.get("K", ""), "none")
        assert layer == case["expected"], case


# ─── my environment variables (`docs/plan-personal-env.md`) ─────────────────

V = {"K": "personal"}
SVC = {"K": "service"}


@pytest.mark.parametrize(
    ("policy", "shared", "private", "personal", "service", "expected"),
    [
        # Shared never reads my environment variables (D2), even with nothing else.
        ("shared_first", {}, {}, V, {}, None),
        ("shared_first", S, P, V, SVC, "shared"),
        ("shared_first", {}, P, V, SVC, "private"),
        ("shared_first", {}, {}, V, SVC, "service"),
        # Private first: this item's own value, then mine, then the service
        # account, then the shared copy (D4, D7).
        ("private_first", S, P, V, SVC, "private"),
        ("private_first", S, {}, V, SVC, "personal"),
        ("private_first", S, {}, {}, SVC, "service"),
        ("private_first", S, {}, {}, {}, "shared"),
        # Private only: the same, without the shared copy.
        ("private_only", S, P, V, SVC, "private"),
        ("private_only", S, {}, V, SVC, "personal"),
        ("private_only", S, {}, {}, SVC, "service"),
        ("private_only", S, {}, {}, {}, None),
    ],
)
def test_each_policy_orders_the_four_sources(policy, shared, private, personal, service, expected):
    got = resolve_env(
        shared=shared, private=private, personal=personal, service=service, policy={"K": policy}
    )
    assert got.get("K") == expected


def test_a_name_only_in_my_variables_does_not_appear_without_a_private_policy():
    """D2: the item must ask. A name it never set a policy for is not passed,
    and neither is one it set to Shared."""
    got = resolve_env(
        shared={}, private={}, personal={"A": "1", "B": "2"}, policy={"B": "shared_first"}
    )
    assert got == {}


def test_an_item_that_never_asked_cannot_tell_from_the_order_that_i_hold_a_name():
    """The names' ORDER reaches a tool (`SANDBOX_USER_ENV_KEYS`). A name in my
    variables that this item does not ask for must not move the others: that
    would tell the item's tools I hold such a name (review round 1, N1)."""
    shared = {"A": "a", "B": "b"}

    without = resolve_env(shared=shared, private={}, policy={})
    with_mine = resolve_env(shared=shared, private={}, personal={"B": "mine"}, policy={})

    assert list(with_mine) == list(without) == ["A", "B"]


def test_splitting_the_service_account_out_changes_nothing_without_my_variables():
    """Parity, with the old composition as the oracle: the service account used
    to ride at the bottom of the private dict (`{**service, **own}`). Every
    placement of three names over the three sources, under every policy."""
    names = ("A", "B", "C")
    policies = ("shared_first", "private_first", "private_only")
    for placement in itertools.product(range(8), repeat=3):
        where = dict(zip(names, placement, strict=True))
        shared = {n: f"s{n}" for n, w in where.items() if w & 1}
        own = {n: f"p{n}" for n, w in where.items() if w & 2}
        service = {n: f"v{n}" for n, w in where.items() if w & 4}
        for pol in itertools.product(policies, repeat=3):
            policy = dict(zip(names, pol, strict=True))
            old = resolve_env(shared=shared, private={**service, **own}, policy=policy)
            new = resolve_env(shared=shared, private=own, service=service, policy=policy)
            assert list(new.items()) == list(old.items()), (shared, own, service, policy)


# ─── a blank value is not a value (docs/plan-env-request-card.md, review 1) ──


@pytest.mark.parametrize(
    ("policy", "shared", "private", "personal", "expected"),
    [
        # A blank shared copy no longer hides the person's own value.
        ("shared_first", {"K": ""}, {"K": "p"}, {}, "p"),
        ("shared_first", {"K": "  "}, {"K": "p"}, {}, "p"),
        # Nor does a blank private value hide the next layer.
        ("private_first", {"K": "s"}, {"K": ""}, {"K": "v"}, "v"),
        ("private_first", {"K": "s"}, {"K": ""}, {}, "s"),
        # Blank everywhere: the tool is not handed the name at all.
        ("shared_first", {"K": ""}, {"K": ""}, {}, None),
    ],
)
def test_a_blank_value_reads_as_not_set(policy, shared, private, personal, expected):  # noqa: ANN001
    got = resolve_env(shared=shared, private=private, personal=personal, policy={"K": policy})

    assert got.get("K") == expected


def test_what_counts_as_blank_is_the_table_both_sides_read():
    """Python's `str.strip()` and JS's `String.trim()` disagree on a dozen
    characters; the tool would get a value the card calls missing. One list,
    held by both (`web/tests/envLayersParity.test.ts`)."""
    import json
    from pathlib import Path

    from workspace_app.api.env_layers import is_blank

    table = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "env_layers_cases.json").read_text()
    )
    for row in table["blanks"]:
        assert is_blank(row["value"]) is row["blank"], repr(row["value"])
