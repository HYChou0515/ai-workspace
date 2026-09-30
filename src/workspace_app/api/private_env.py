"""The PRIVATE environment layer's storage (`docs/plan-wui-viewer-login.md`).

One row per (person, item): the values that person keeps for that item's tools
— typed by hand, filled by a login, or written for them by the deploy's
``IRequestEnv``. The SHARED layer is the item's ``env_vars``; which of the two a
tool gets for a given name is the item's ``env_policy`` (`env_layers`).

Why each part is the way it is:

* **On the server, not in the browser.** A turn that a peer pod re-runs after
  this one died has no request and no browser behind it; the only place its
  values can come from is a shared backend.
* **Keyed by item, not by person alone.** Every tool an item runs is handed the
  whole resolved env (`tooling.registry._tool_env`), so a token logged in for
  one item would otherwise reach every other item's tools — including
  third-party ones the person never chose.
* **Addressed only as "mine".** The routes take no user parameter: the row is
  always the caller's. Nobody, a superuser included, can name someone else's,
  so "only you can read this" is a property of the shape, not of a check.

Registered post-``spec.apply`` so specstar emits no auto-CRUD routes for it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Response
from msgspec import Struct
from pydantic import BaseModel
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from .locator import ItemLocator
from .timeutil import now_ms


class PrivateEnv(Struct):
    user_id: str
    item_id: str
    values: dict[str, str]
    updated_at: int


#: specstar ids cannot hold `/`; U+2215 is what `wui_deploy` uses in its place.
#: Both halves are percent-quoted, which turns a U+2215 inside either into
#: `%E2%88%95`, so the one bare separator is unambiguous.
_SEP = "∕"


def private_env_id(user_id: str, item_id: str) -> str:
    return quote(user_id, safe="") + _SEP + quote(item_id, safe="")


def register_private_env(spec: SpecStar) -> None:
    with contextlib.suppress(ValueError):
        spec.add_model(PrivateEnv, indexed_fields=["item_id"])


class PrivateEnvStore:
    def __init__(self, spec: SpecStar, *, now: Callable[[], int] = now_ms) -> None:
        self._spec = spec
        self._now = now

    def _rm(self):
        return self._spec.get_resource_manager(PrivateEnv)

    def get(self, user_id: str, item_id: str) -> dict[str, str]:
        try:
            data = self._rm().get(private_env_id(user_id, item_id)).data
        except ResourceIDNotFoundError:
            return {}
        assert isinstance(data, PrivateEnv)  # narrow for ty (coverage-clean)
        return dict(data.values)

    def replace(self, user_id: str, item_id: str, values: dict[str, str]) -> None:
        rm = self._rm()
        rid = private_env_id(user_id, item_id)
        row = PrivateEnv(user_id=user_id, item_id=item_id, values=values, updated_at=self._now())
        try:
            rm.get(rid)
        except ResourceIDNotFoundError:
            rm.create(row, resource_id=rid)
            return
        rm.update(rid, row)

    def merge(self, user_id: str, item_id: str, fresh: dict[str, str]) -> dict[str, str]:
        """Write ``fresh`` over the row — last write wins, per name — and return
        the whole row. A name ``fresh`` does not carry is left as it was, so a
        seam re-writing what it provides on every request never erases a value
        the person typed. Skips the write when nothing changed: this runs on
        every send and every page tool call."""
        stored = self.get(user_id, item_id)
        merged = {**stored, **fresh}
        if merged != stored:
            self.replace(user_id, item_id, merged)
        return merged

    def clear(self, user_id: str, item_id: str) -> None:
        """Hard delete — logging out must leave nothing behind. Absent is fine:
        a second logout is the state asked for."""
        with contextlib.suppress(ResourceIDNotFoundError):
            self._rm().permanently_delete(private_env_id(user_id, item_id))

    def purge_item(self, item_id: str) -> None:
        """Every person's row for one item, permanently — the item-delete
        cascade's step. Nobody else could reach these rows to remove them."""
        rm = self._rm()
        for res in rm.list_resources((QB["item_id"] == item_id).build()):
            rid = res.info.resource_id
            assert isinstance(rid, str)
            with contextlib.suppress(ResourceIDNotFoundError):
                rm.permanently_delete(rid)


async def private_layer(
    store: PrivateEnvStore | None, *, user_id: str, item_id: str, fresh: dict[str, str]
) -> dict[str, str]:
    """A person's PRIVATE env layer for one item, as a tool about to run for
    them gets it: what the deploy's seam just said about them (``fresh``)
    written over their stored row — last write wins, per name — then the whole
    row. The ONE composition both the chat send and a page's ``callTool`` use,
    so the two cannot drift. Off the loop: specstar I/O on a request path.

    ``store`` None (a composition that wired none) ⇒ the layer is exactly what
    the seam answered, as before this plan."""
    if store is None:
        return fresh
    return await asyncio.to_thread(store.merge, user_id, item_id, fresh)


async def unattended_layer(
    store: PrivateEnvStore | None, *, headless: dict[str, str], acting_for: str, item_id: str
) -> dict[str, str]:
    """The PRIVATE layer of a turn with no request behind it: the seam's
    request-less answer (``env_without_request`` — a service account, or
    nothing) with ``acting_for``'s own stored row over it. ``acting_for`` is
    the PERSON the turn runs for — the presser of a page button, the starter
    of a run, the setter of a goal — which is not necessarily who the turn is
    attributed or billed to. Empty ``acting_for`` (an unbound schedule, an
    entity trigger) ⇒ the seam's answer alone: nobody's private values."""
    if store is None or not acting_for:
        return headless
    stored = await asyncio.to_thread(store.get, acting_for, item_id)
    return {**headless, **stored}


class PrivateValues(BaseModel):
    values: dict[str, str]


def register_private_env_routes(
    app: FastAPI | APIRouter,
    *,
    store: PrivateEnvStore,
    locator: ItemLocator,
    get_user_id: Callable[[], str],
) -> None:
    @app.get("/a/{slug}/items/{item_id}/env/private", response_model=PrivateValues)
    async def get_private_env(slug: str, item_id: str) -> PrivateValues:
        workspace_id = locator.require_access(slug, item_id, "read_meta")
        return PrivateValues(values=store.get(get_user_id(), workspace_id))

    @app.put("/a/{slug}/items/{item_id}/env/private", response_model=PrivateValues)
    async def put_private_env(slug: str, item_id: str, body: PrivateValues) -> PrivateValues:
        workspace_id = locator.require_access(slug, item_id, "read_meta")
        store.replace(get_user_id(), workspace_id, dict(body.values))
        return PrivateValues(values=dict(body.values))

    @app.delete("/a/{slug}/items/{item_id}/env/private", status_code=204)
    async def delete_private_env(slug: str, item_id: str) -> Response:
        """Log out. NOT gated on the item: it touches only the caller's own row,
        and someone who has lost access must still be able to take their
        credential back out."""
        store.clear(get_user_id(), item_id)
        return Response(status_code=204)
