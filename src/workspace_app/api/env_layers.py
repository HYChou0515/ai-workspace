"""Which layer a tool's variable comes from (`docs/plan-wui-viewer-login.md`).

Two layers feed a tool's environment:

* SHARED — the item's ``env_vars``: one copy, readable by everyone who can open
  the item, written by whoever holds ``write_meta``.
* PRIVATE — the person the tool runs for: their own values for this item, plus
  what the deploy's ``IRequestEnv`` said about them. Nobody else reads it.

Each name on the item may carry a policy (``WorkItemBase.env_policy``) choosing
between them. A name with no policy is ``shared_first`` — the merge every turn
did before the private layer existed (`{**caller_env, **item_env}`, #714), so
an item nobody has touched behaves exactly as it did.

A policy decides WHERE a value comes from, never WHETHER it must exist: a name
neither layer supplies is left out, the same as a name nobody set. Whether a
tool can run without it is the tool's own business.
"""

from __future__ import annotations

SHARED_FIRST = "shared_first"
PRIVATE_FIRST = "private_first"
PRIVATE_ONLY = "private_only"

POLICIES = (SHARED_FIRST, PRIVATE_FIRST, PRIVATE_ONLY)


def resolve_env(
    *, shared: dict[str, str], private: dict[str, str], policy: dict[str, str]
) -> dict[str, str]:
    """The environment a tool sees, one name at a time.

    An unrecognised policy string reads as the default: the item is a plain
    PATCH-able record, and a stray value must not become a behaviour nobody
    wrote."""
    env: dict[str, str] = {}
    # Walked in the old merge's key order: `_tool_env` joins the names into
    # `SANDBOX_USER_ENV_KEYS`, so order is something a tool can see.
    for name in {**private, **shared}:
        rule = policy.get(name, SHARED_FIRST)
        if rule == PRIVATE_ONLY:
            order = (private,)
        elif rule == PRIVATE_FIRST:
            order = (private, shared)
        else:
            order = (shared, private)
        for layer in order:
            if name in layer:
                env[name] = layer[name]
                break
    return env
