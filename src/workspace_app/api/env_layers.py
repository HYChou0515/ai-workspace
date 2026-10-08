"""Which layer a tool's variable comes from (`docs/plan-wui-viewer-login.md`,
`docs/plan-personal-env.md`).

Two layers feed a tool's environment:

* SHARED — the item's ``env_vars``: one copy, readable by everyone who can open
  the item, written by whoever holds ``write_meta``.
* PRIVATE — the person the tool runs for. Three sources, most specific first:
  their own values for THIS item (with what the deploy's ``IRequestEnv`` said
  about them over those), their values for every item ("my environment
  variables"), and — when nobody is at the request — the deploy's service
  account (``env_without_request``). Nobody else reads any of them.

Each name on the item may carry a policy (``WorkItemBase.env_policy``) choosing
between them. A name with no policy is ``shared_first`` — the merge every turn
did before the private layer existed (`{**caller_env, **item_env}`, #714), so
an item nobody has touched behaves exactly as it did.

My environment variables are read ONLY for a name whose policy asks for a
personal value (``private_first`` / ``private_only``): they hold the person's
values for every item, so an item that never asked for a name does not get it.

A policy decides WHERE a value comes from, never WHETHER it must exist: a name
neither layer supplies is left out, the same as a name nobody set. Whether a
tool can run without it is the tool's own business.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SHARED_FIRST = "shared_first"
PRIVATE_FIRST = "private_first"
PRIVATE_ONLY = "private_only"

POLICIES = (SHARED_FIRST, PRIVATE_FIRST, PRIVATE_ONLY)


@dataclass(frozen=True)
class PersonEnv:
    """The private side of one resolution: who the tool runs for, in its three
    sources. Kept apart until ``resolve_env`` because the item's policy decides
    per name whether ``personal`` is consulted at all, and the service account
    must rank BELOW the person's own values."""

    #: Their values for this item — what they typed, the seam's answer over it.
    own: dict[str, str] = field(default_factory=dict)
    #: Their values for every item ("my environment variables").
    personal: dict[str, str] = field(default_factory=dict)
    #: The deploy's request-less answer, for a turn nobody is at.
    service: dict[str, str] = field(default_factory=dict)


def resolve(*, shared: dict[str, str], person: PersonEnv | None, policy: dict[str, str]):
    """``resolve_env`` for a ``PersonEnv`` — the one shape every entry point hands over."""
    p = person or PersonEnv()
    return resolve_env(
        shared=shared, private=p.own, personal=p.personal, service=p.service, policy=policy
    )


#: What a blank value is made of — and nothing else: `str.strip()` strips 23
#: characters beyond these six and JS's `String.trim()` 19, disagreeing on 6
#: (U+001C–001F and U+0085 only Python, U+FEFF only JS), and a value
#: one side sees as blank the other would hand to a tool. Held to the same list
#: as the FE's `isBlank` by `tests/fixtures/env_layers_cases.json` (`blanks`).
_BLANK = " \t\n\r\f\v"


def is_blank(value: str) -> bool:
    """A value that counts as not set (plan-env-request-card N5)."""
    return not value.strip(_BLANK)


def resolve_env(
    *,
    shared: dict[str, str],
    private: dict[str, str],
    policy: dict[str, str],
    personal: dict[str, str] | None = None,
    service: dict[str, str] | None = None,
) -> dict[str, str]:
    """The environment a tool sees, one name at a time.

    | policy          | order                                              |
    |-----------------|----------------------------------------------------|
    | ``shared_first``  | shared → private → service                       |
    | ``private_first`` | private → personal → service → shared            |
    | ``private_only``  | private → personal → service                     |

    A blank value (empty or whitespace) counts as not set, in every layer.

    An unrecognised policy string reads as the default: the item is a plain
    PATCH-able record, and a stray value must not become a behaviour nobody
    wrote."""
    service = service or {}
    # Not consulted by `shared_first`: that order is where "an item that never
    # asked for a name does not get it" (D2) is decided — once, here.
    personal = personal or {}
    env: dict[str, str] = {}
    # Walked in the old merge's key order (the service account sat at the bottom
    # of the private dict): `_tool_env` joins the names into
    # `SANDBOX_USER_ENV_KEYS`, so order is something a tool can see. Only the
    # personal names this item could use take part in the walk — one it never
    # asked for must not move the others, or the order would tell its tools I
    # hold such a name (round 1, N1).
    asked = {n: v for n, v in personal.items() if policy.get(n) in (PRIVATE_FIRST, PRIVATE_ONLY)}
    for name in {**service, **asked, **private, **shared}:
        rule = policy.get(name, SHARED_FIRST)
        if rule == PRIVATE_ONLY:
            order = (private, personal, service)
        elif rule == PRIVATE_FIRST:
            order = (private, personal, service, shared)
        else:
            order = (shared, private, service)
        for layer in order:
            # A blank value is not a value: it does not hide the next layer, and
            # a name blank everywhere is not handed to the tool at all.
            if not is_blank(layer.get(name, "")):
                env[name] = layer[name]
                break
    return env
