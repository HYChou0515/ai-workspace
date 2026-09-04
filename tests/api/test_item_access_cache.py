"""Authorization is the whole database cost of a read request.

After the id-prefix fix a `GET /files` makes four specstar round-trips, and every
one of them is `require_access` deciding whether the caller may look: the item
(for its Permission), its meta (for `created_by`), the caller's groups. The
handler itself makes none.

That matters because the cost is CPU-bound Python, not SQL — a cached, zero-SQL
`get` measured 28ms in production — so it cannot be parallelised away by threads
(the GIL) and it is paid again on every request of every user action.
"""

from __future__ import annotations

import contextlib

from workspace_app.api.locator import ItemLocator
from workspace_app.apps.catalog import AppCatalog
from workspace_app.apps.pm.model import PmProject
from workspace_app.config.schema import Settings
from workspace_app.resources import make_spec


def _locator(spec, user: str = "u") -> ItemLocator:
    return ItemLocator(
        spec,
        AppCatalog(presets=Settings().agents.presets),
        get_user_id=lambda: user,
    )


def test_repeated_access_checks_hit_the_database_once() -> None:
    """A user action fires several requests at one item; each re-derived the same
    answer from scratch. The facts behind it — the item's Permission and owner —
    change far more slowly than a request arrives."""
    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(PmProject)
    item_id = rm.create(PmProject(title="t", owner="u")).resource_id
    locator = _locator(spec)

    calls: list[str] = []
    original = type(rm).get

    def counting_get(self, *args, **kwargs):  # noqa: ANN001
        calls.append(self.resource_name)
        return original(self, *args, **kwargs)

    type(rm).get = counting_get  # ty: ignore[invalid-assignment]
    try:
        for _ in range(4):
            assert locator.require_access("pm", item_id, "read_content") == item_id
    finally:
        type(rm).get = original

    assert len(calls) == 1, calls


def test_a_permission_change_is_not_hidden_by_the_cache() -> None:
    """A cache that outlives a revocation is a security bug, not a slow one. The
    setter forgets the item, so the very next request re-reads it — the window
    only ever covers requests nobody changed anything during."""
    spec = make_spec(default_user="owner")
    rm = spec.get_resource_manager(PmProject)
    item_id = rm.create(PmProject(title="t", owner="owner")).resource_id
    stranger = _locator(spec, user="stranger")

    assert stranger.require_access("pm", item_id, "read_content") == item_id  # public

    from workspace_app.perm.model import Permission

    with rm.using("owner"):
        rm.update(
            item_id,
            PmProject(title="t", owner="owner", permission=Permission(visibility="private")),
        )
    stranger.forget_access(item_id)

    try:
        stranger.require_access("pm", item_id, "read_content")
    except Exception as exc:  # noqa: BLE001 — an HTTPException of either code is a refusal
        assert getattr(exc, "status_code", None) in (403, 404), exc
    else:
        raise AssertionError("a private item stayed readable to a stranger")


def test_the_access_memo_is_a_cache_and_not_a_map() -> None:
    """Round-12, and the one finding of that round an operator can reach.

    This memo is new on the branch that added per-item sandbox sizing. It holds
    a full copy of every item record it has ever gated, for the life of the
    process: the 5-second window decides whether an entry is TRUSTED, not
    whether it is kept, and `forget_access` is called from exactly one route.
    A long-lived pod that gates many items therefore grows without limit.

    Its sibling one file over got this right — `api/app.py`'s `_item_facts`
    carries `_ITEM_FACT_MAX` and a comment reading "bounded: this is a cache,
    not a map". Same rule, two carriers, and only one of them had it. That is
    the shape this whole branch keeps producing.

    Asserted as a CEILING on the dict, not on memory: what went wrong is
    unbounded growth, and the cheapest true statement about it is that the
    number of entries stops going up.
    """
    from workspace_app.api import locator as locator_mod

    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(PmProject)
    locator = _locator(spec)

    for _ in range(locator_mod._ACCESS_MAX + 50):
        item_id = rm.create(PmProject(title="t", owner="u")).resource_id
        assert locator.require_access("pm", item_id, "read_content") == item_id

    assert len(locator._access) <= locator_mod._ACCESS_MAX, len(locator._access)


def test_the_groups_memo_is_bounded_too() -> None:
    """The SECOND carrier of the same rule, asked because the first one had it
    wrong. `require_access` memoises two things — the item's facts and the
    caller's groups — and both arrived on this branch (master's `require_access`
    delegates straight through and caches nothing). Bounding one and not the
    other is how a rule stops being true; a pod that serves many people grows
    the group memo exactly the way the item memo grew.
    """
    from workspace_app.api import locator as locator_mod

    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(PmProject)
    item_id = rm.create(PmProject(title="t", owner="u")).resource_id
    who = {"id": "u"}
    locator = ItemLocator(
        spec,
        AppCatalog(presets=Settings().agents.presets),
        get_user_id=lambda: who["id"],
    )

    for n in range(locator_mod._GROUPS_MAX + 50):
        who["id"] = f"person-{n}"
        with contextlib.suppress(Exception):  # a stranger is refused; the memo still fills
            locator.require_access("pm", item_id, "read_content")

    assert len(locator._groups) <= locator_mod._GROUPS_MAX, len(locator._groups)


def test_a_miss_is_not_cached_so_an_item_created_a_moment_later_is_seen() -> None:
    """R12-6. The memo keeps POSITIVE answers only, and that asymmetry is the
    whole reason it is safe to have at all.

    "No such item" is the one result that goes stale in the direction that
    breaks things: a workflow that addresses the item it just created would
    keep 404-ing for the rest of the window. A permission is a fact about a
    thing that exists; absence is not.

    The guard was live and untested — six lines, so "unreachable anyway" was
    never the reason.
    """
    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(PmProject)
    locator = _locator(spec)
    item_id = "pm-project:not-yet"

    with contextlib.suppress(Exception):
        locator.require_access("pm", item_id, "read_content")

    made = rm.create(PmProject(title="t", owner="u"), resource_id=item_id).resource_id

    assert locator.require_access("pm", made, "read_content") == made
