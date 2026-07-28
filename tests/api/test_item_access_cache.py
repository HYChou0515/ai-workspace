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
