"""The PRIVATE environment layer's storage (`docs/plan-wui-viewer-login.md`).

Per (person, item), two rows:

* ``PrivateEnv`` — what the person put there themselves: typed in "Only me", or
  filled by a sign-in (``IEnvProvider``). Changed only by them.
* ``PrivateSeam`` — the deploy's ``IRequestEnv.env_for`` answer about them, as of
  their last chat send or page ``callTool`` in this item (the only two paths
  that ask it): replaced whole every time it changes, never merged.

The SHARED layer is the item's ``env_vars``; which of the two layers a tool gets
for a given name is the item's ``env_policy`` (`env_layers`). Within the private
layer the seam's answer wins a name: it is the automatic, always-current value
(Q5), and a name it stops returning stops reaching tools from that request
on. Signing out of SSO is not a request this app sees, so until the person's
next one here, work done for them with nobody present (goal rounds, re-runs,
runs they pressed, schedules they bound) keeps the last answer (round 2
veracity, V2).

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
* **The seam's answer in its OWN row** (review round 1): merged into one row it
  kept names the seam had stopped returning, reordered the names a tool sees,
  and a seam write racing a sign-out rewrote the values just cleared. Apart,
  a seam write never touches what the person put there.
* **No history.** Every write is one replace followed by pruning the older
  revisions: a plain update keeps each past value — each rotated token — as a
  readable revision, and a delete-then-create left a moment with no row.

Registered post-``spec.apply`` so specstar emits no auto-CRUD routes for either.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from msgspec import Struct
from pydantic import BaseModel
from specstar import QB, SpecStar
from specstar.types import DuplicateResourceError, ResourceIDNotFoundError

from ..perm import Verb
from .env_layers import PRIVATE_FIRST, PRIVATE_ONLY, PersonEnv
from .locator import ItemLocator
from .timeutil import now_ms

logger = logging.getLogger(__name__)


class PrivateEnv(Struct):
    user_id: str
    item_id: str
    values: dict[str, str]
    updated_at: int
    #: The names in the order they were given: the store canonicalises a dict's
    #: key order, and the order becomes ``SANDBOX_USER_ENV_KEYS`` (round 2, R3).
    names: list[str] = []


class PrivateSeam(Struct):
    user_id: str
    item_id: str
    values: dict[str, str]
    updated_at: int
    names: list[str] = []


class PersonalEnv(Struct):
    """A person's values for EVERY item — "my environment variables"
    (`docs/plan-personal-env.md`). One row per person, keyed by them alone. An
    item uses a name from here only when its policy asks for a personal value
    (Private first / Private only), so a token set here does not reach the
    tools of an item that never asked for it."""

    user_id: str
    values: dict[str, str]
    #: When each name was last given a NEW value (epoch ms): the page shows
    #: "signed in 3 days ago", which a rewrite of the whole row must not reset.
    updated: dict[str, int]
    names: list[str] = []


#: specstar ids cannot hold `/`; U+2215 is what `wui_deploy` uses in its place.
#: Both halves are percent-quoted, which turns a U+2215 inside either into
#: `%E2%88%95`, so the one bare separator is unambiguous.
_SEP = "∕"


def private_env_id(user_id: str, item_id: str) -> str:
    return quote(user_id, safe="") + _SEP + quote(item_id, safe="")


def register_private_env(spec: SpecStar) -> None:
    for model in (PrivateEnv, PrivateSeam):
        with contextlib.suppress(ValueError):
            spec.add_model(model, indexed_fields=["item_id"])
    with contextlib.suppress(ValueError):
        spec.add_model(PersonalEnv)


class PrivateEnvStore:
    def __init__(
        self,
        spec: SpecStar,
        *,
        now: Callable[[], int] = now_ms,
        may: Callable[[str, str, Verb], bool] | None = None,
        seam_enabled: bool = True,
    ) -> None:
        self._spec = spec
        self._now = now
        #: False when the deploy has no ``IRequestEnv``: a seam answer stored
        #: while one WAS configured must not keep reaching turns after it is
        #: removed (review round 2) — nothing would ever rewrite it.
        self._seam_enabled = seam_enabled
        #: ``may(user, item_id, verb)`` — asked before a person's values are used
        #: with nobody at the request (review round 1, C2/F1): removed from the
        #: item, they must stop lending them. None ⇒ unchecked.
        self._may = may

    def may(self, user_id: str, item_id: str, verb: Verb) -> bool:
        return self._may is None or self._may(user_id, item_id, verb)

    def _read(self, model: type, user_id: str, item_id: str) -> dict[str, str]:
        try:
            data = self._spec.get_resource_manager(model).get(private_env_id(user_id, item_id)).data
        except ResourceIDNotFoundError:
            return {}
        assert isinstance(data, PrivateEnv | PrivateSeam)
        ordered = [n for n in data.names if n in data.values]
        rest = [n for n in data.values if n not in ordered]
        return {n: data.values[n] for n in [*ordered, *rest]}

    def _write(self, model: type, user_id: str, item_id: str, values: dict[str, str]) -> None:
        """ONE replace, then prune the older revisions — so a reader sees the old
        value or the new one, never a moment with no row (round 2, D4: a
        delete-then-create let a goal round in between run without the
        person's credential), and no revision keeps a replaced value (R4).
        Empty ⇒ no row at all. Concurrent writers: the last one wins."""
        rm = self._spec.get_resource_manager(model)
        rid = private_env_id(user_id, item_id)
        if not values:
            with contextlib.suppress(ResourceIDNotFoundError):
                rm.permanently_delete(rid)
            return
        row = model(
            user_id=user_id,
            item_id=item_id,
            values=values,
            updated_at=self._now(),
            names=list(values),
        )
        try:
            rm.update(rid, row)
        except ResourceIDNotFoundError:
            try:
                rm.create(row, resource_id=rid)
            except DuplicateResourceError:  # a concurrent first write got there
                rm.update(rid, row)
        # A sign-out may land between the write and this (round 3, defect 3):
        # the row is gone, with every revision — nothing left to prune.
        with contextlib.suppress(ResourceIDNotFoundError):
            rm.prune_revisions(rid, keep_last_n=1)

    def get(self, user_id: str, item_id: str) -> dict[str, str]:
        """What the person put there themselves."""
        return self._read(PrivateEnv, user_id, item_id)

    def seam(self, user_id: str, item_id: str) -> dict[str, str]:
        """The deploy's ``env_for`` answer about them, as of their last request —
        nothing when the deploy has no seam (any row is left over from one)."""
        if not self._seam_enabled:
            return {}
        return self._read(PrivateSeam, user_id, item_id)

    def replace(self, user_id: str, item_id: str, values: dict[str, str]) -> None:
        self._write(PrivateEnv, user_id, item_id, values)

    def record_seam(self, user_id: str, item_id: str, fresh: dict[str, str]) -> None:
        """Keep the seam's latest answer — whole, so a name it dropped is gone.
        Skips the write when nothing changed — ORDER included, since tools see
        the names in the order given (round 3, defect 4): this runs on every
        send and every page tool call."""
        if list(self.seam(user_id, item_id).items()) != list(fresh.items()):
            self._write(PrivateSeam, user_id, item_id, fresh)

    def clear(self, user_id: str, item_id: str) -> None:
        """Forget both rows. What the seam provides comes back at the person's
        next request — it is still what their request says about them."""
        for model in (PrivateEnv, PrivateSeam):
            with contextlib.suppress(ResourceIDNotFoundError):
                self._spec.get_resource_manager(model).permanently_delete(
                    private_env_id(user_id, item_id)
                )

    def item_values_named(self, names: frozenset[str]) -> list[tuple[str, str, list[str]]]:
        """(person, item, the names held) for every per-item row holding any of
        ``names`` — the candidates the sign-in cleanup then narrows to what
        hides one in my environment variables. Values stay here."""
        out: list[tuple[str, str, list[str]]] = []
        for res in self._spec.get_resource_manager(PrivateEnv).list_resources():
            data = res.data
            assert isinstance(data, PrivateEnv)
            held = [n for n in data.values if n in names]
            if held:
                out.append((data.user_id, data.item_id, held))
        return out

    def drop_names(self, user_id: str, item_id: str, names: list[str]) -> None:
        """Take these names out of one person's values for one item, keeping
        the rest in their order."""
        kept = {n: v for n, v in self.get(user_id, item_id).items() if n not in names}
        self.replace(user_id, item_id, kept)

    # ── my environment variables (`docs/plan-personal-env.md`) ───────────────

    def _personal_row(self, user_id: str) -> PersonalEnv | None:
        try:
            data = self._spec.get_resource_manager(PersonalEnv).get(quote(user_id, safe="")).data
        except ResourceIDNotFoundError:
            return None
        assert isinstance(data, PersonalEnv)
        return data

    def personal(self, user_id: str) -> dict[str, str]:
        """The person's values for every item, in the order they were given."""
        row = self._personal_row(user_id)
        if row is None:
            return {}
        ordered = [n for n in row.names if n in row.values]
        rest = [n for n in row.values if n not in ordered]
        return {n: row.values[n] for n in [*ordered, *rest]}

    def personal_updated(self, user_id: str) -> dict[str, int]:
        row = self._personal_row(user_id)
        return {} if row is None else {n: row.updated[n] for n in row.values if n in row.updated}

    def replace_personal(self, user_id: str, values: dict[str, str]) -> None:
        """The whole row, written the way the per-item rows are: one replace,
        then the older revisions pruned, so a rotated token leaves nothing
        readable behind. A name keeps its time unless its value changed."""
        rm = self._spec.get_resource_manager(PersonalEnv)
        rid = quote(user_id, safe="")
        if not values:
            with contextlib.suppress(ResourceIDNotFoundError):
                rm.permanently_delete(rid)
            return
        before = self._personal_row(user_id)
        now = self._now()
        updated = {
            n: before.updated[n]
            if before is not None and before.values.get(n) == v and n in before.updated
            else now
            for n, v in values.items()
        }
        row = PersonalEnv(user_id=user_id, values=values, updated=updated, names=list(values))
        try:
            rm.update(rid, row)
        except ResourceIDNotFoundError:
            try:
                rm.create(row, resource_id=rid)
            except DuplicateResourceError:  # a concurrent first write got there
                rm.update(rid, row)
        with contextlib.suppress(ResourceIDNotFoundError):
            rm.prune_revisions(rid, keep_last_n=1)

    def purge_item(self, item_id: str) -> None:
        """Every person's rows for one item, permanently — the item-delete
        cascade's step. Nobody else could reach these rows to remove them."""
        for model in (PrivateEnv, PrivateSeam):
            rm = self._spec.get_resource_manager(model)
            for res in rm.list_resources((QB["item_id"] == item_id).build()):
                rid = res.info.resource_id  # ty: ignore[unresolved-attribute]
                assert isinstance(rid, str)
                with contextlib.suppress(ResourceIDNotFoundError):
                    rm.permanently_delete(rid)


def own_layer(typed: dict[str, str], seam: dict[str, str]) -> dict[str, str]:
    """A person's private layer: what they typed, with the seam's answer over it
    (the automatic value wins a name, Q5). ONE function both compositions below
    call, and the one the FE's copy is held to
    (`tests/fixtures/private_layer_cases.json`) — order included, since the
    names become ``SANDBOX_USER_ENV_KEYS``."""
    return {**typed, **seam}


async def private_layer(
    store: PrivateEnvStore | None, *, user_id: str, item_id: str, fresh: dict[str, str] | None
) -> PersonEnv:
    """A person's PRIVATE layer for a turn or tool call WITH a request behind it:
    what they put there themselves, with what the deploy's seam just said about
    them over it (``fresh``; ``None`` = no seam configured). The seam's answer is
    kept (whole) for their turns with no request behind them.

    The ONE composition the chat send and a page's ``callTool`` share, so the
    two cannot drift. Off the loop: specstar I/O on a request path.

    ``store`` None (a composition that wired none) ⇒ exactly the seam's answer,
    as before this plan. Order: typed names, then the seam's in ITS order — with
    nothing typed that is the seam's order, as before (the names become
    ``SANDBOX_USER_ENV_KEYS``, which a tool can see).

    Their values for every item ride beside it (`plan-personal-env`): which of
    those a tool gets is the item's policy, decided in ``resolve_env``."""
    seam = fresh or {}
    if store is None:
        return PersonEnv(own=dict(seam))
    if fresh is not None:
        await asyncio.to_thread(store.record_seam, user_id, item_id, fresh)
    typed = await asyncio.to_thread(store.get, user_id, item_id)
    personal = await asyncio.to_thread(store.personal, user_id)
    return PersonEnv(own=own_layer(typed, seam), personal=personal)


async def unattended_layer(
    store: PrivateEnvStore | None,
    *,
    headless: dict[str, str],
    acting_for: str,
    item_id: str,
    verb: Verb,
) -> PersonEnv:
    """The PRIVATE layer of a turn with no request behind it: the seam's
    request-less answer (``env_without_request`` — a service account, or
    nothing), with ``acting_for``'s own values over it — what they put there,
    then the seam's last answer about them. ``acting_for`` is the PERSON the
    turn runs for (the presser of a page button, the starter of a run, the
    binder of a schedule, the setter of a goal), which is not necessarily who
    the turn is attributed or billed to. Empty ``acting_for`` (an unbound
    schedule, an entity trigger) ⇒ the seam's answer alone.

    ⚠️ A person's value BEATS the service account on the same name: this path
    runs FOR them, and their own credential is what they would have used.

    ``verb`` is what ``acting_for`` must STILL hold on the item — asked here,
    at use, because nobody is at the request to be gated: a person removed from
    the item stops lending their values at the next turn (review round 1) — the
    ones for this item and the ones for every item alike (`plan-personal-env`).

    The service account is kept apart (``service``) rather than merged under
    their values, so their values for every item can rank above it too."""
    if store is None or not acting_for:
        return PersonEnv(service=dict(headless))
    if not await asyncio.to_thread(store.may, acting_for, item_id, verb):
        return PersonEnv(service=dict(headless))
    typed = await asyncio.to_thread(store.get, acting_for, item_id)
    seam = await asyncio.to_thread(store.seam, acting_for, item_id)
    personal = await asyncio.to_thread(store.personal, acting_for)
    return PersonEnv(own=own_layer(typed, seam), personal=personal, service=dict(headless))


class PrivateValues(BaseModel):
    values: dict[str, str]


class MineOut(BaseModel):
    #: What the person put there themselves — editable in "Only me".
    values: dict[str, str]
    #: What the deploy's seam said about them at their last request — shown,
    #: not editable: it is rewritten by their next request anyway.
    auto: dict[str, str]


class ItemLayers(BaseModel):
    shared: dict[str, str]
    policy: dict[str, str]


class CleanupBody(BaseModel):
    apply: bool = False


class CleanupRow(BaseModel):
    user_id: str
    item_id: str
    #: The names that go — never their values.
    names: list[str]


class CleanupOut(BaseModel):
    applied: bool
    rows: list[CleanupRow]


class PersonalOut(BaseModel):
    values: dict[str, str]
    #: When each name last got a new value (epoch ms).
    updated: dict[str, int]


def register_private_env_routes(
    app: FastAPI | APIRouter,
    *,
    store: PrivateEnvStore,
    locator: ItemLocator,
    get_user_id: Callable[[], str],
    superusers: frozenset[str] = frozenset(),
) -> None:
    @app.get("/a/{slug}/items/{item_id}/env/layers", response_model=ItemLayers)
    async def get_env_layers(slug: str, item_id: str) -> ItemLayers:
        """The item's SHARED values and per-name policy — for a page opened at
        its own address, which has the item's id but not its record. The same
        two fields `read_meta` already returns on the item, under the same verb."""
        workspace_id = locator.require_access(slug, item_id, "read_meta")
        layers = await asyncio.to_thread(locator.env_layers_of, workspace_id)
        return ItemLayers(shared=layers.shared, policy=layers.policy)

    @app.get("/a/{slug}/items/{item_id}/env/private", response_model=MineOut)
    async def get_private_env(slug: str, item_id: str) -> MineOut:
        workspace_id = locator.require_access(slug, item_id, "read_meta")
        me = get_user_id()
        values = await asyncio.to_thread(store.get, me, workspace_id)
        auto = await asyncio.to_thread(store.seam, me, workspace_id)
        return MineOut(values=values, auto=auto)

    @app.put("/a/{slug}/items/{item_id}/env/private", response_model=PrivateValues)
    async def put_private_env(slug: str, item_id: str, body: PrivateValues) -> PrivateValues:
        workspace_id = locator.require_access(slug, item_id, "read_meta")
        await asyncio.to_thread(store.replace, get_user_id(), workspace_id, dict(body.values))
        return PrivateValues(values=dict(body.values))

    @app.delete("/a/{slug}/items/{item_id}/env/private", status_code=204)
    async def delete_private_env(slug: str, item_id: str) -> Response:
        """Log out. NOT gated on the item: it touches only the caller's own rows,
        and someone who has lost access must still be able to take their
        credential back out."""
        await asyncio.to_thread(store.clear, get_user_id(), item_id)
        return Response(status_code=204)

    # My environment variables: no item, no user parameter — always the caller's.

    async def _personal_out(me: str) -> PersonalOut:
        values = await asyncio.to_thread(store.personal, me)
        updated = await asyncio.to_thread(store.personal_updated, me)
        return PersonalOut(values=values, updated=updated)

    @app.get("/me/env", response_model=PersonalOut)
    async def get_personal_env() -> PersonalOut:
        return await _personal_out(get_user_id())

    @app.put("/me/env", response_model=PersonalOut)
    async def put_personal_env(body: PrivateValues) -> PersonalOut:
        me = get_user_id()
        await asyncio.to_thread(store.replace_personal, me, dict(body.values))
        return await _personal_out(me)

    @app.delete("/me/env", status_code=204)
    async def delete_personal_env() -> Response:
        await asyncio.to_thread(store.replace_personal, get_user_id(), {})
        return Response(status_code=204)

    @app.post("/admin/env/clear-item-sign-ins", response_model=CleanupOut)
    async def clear_item_sign_ins(body: CleanupBody, request: Request) -> CleanupOut:
        """`plan-personal-env` D10/A9: remove, from every person's values for
        single items, the names a deploy sign-in produces — where the old value
        hides one there: the item's policy for the name is Private first /
        Private only and the person holds it in my environment variables too.
        Before "my environment variables" a sign-in wrote its token into that
        one item, and an item's own value wins a name — so there those old
        tokens would shadow a new sign-in. A Shared item reads only its own
        value, and someone who has not signed in again has no other, so both
        keep theirs. Run when the operator chooses
        (`scripts/clear_item_sign_ins.py`); dry run unless ``apply``.

        The names come from the providers this API loaded — the deploy's list,
        not one kept by hand. Values are never returned. Superusers only: it
        writes everyone's rows."""
        if get_user_id() not in superusers:
            raise HTTPException(status_code=403, detail="superusers only")
        names = _sign_in_names(getattr(request.app.state, "env_providers", ()) or ())
        held = await asyncio.to_thread(store.item_values_named, names)
        # Only a value that HIDES one (by presence, not by date): where the
        # item asks for a personal value (a Shared item never reads my
        # environment variables, so the value stored in it is the one its
        # tools USE — round 1, F1) and the person
        # holds that name there too (until they sign in on their page, the
        # item's value is the only one their tools have — round 2, F1).
        found = []
        for user_id, item_id, held_names in held:
            policy = (await asyncio.to_thread(locator.env_layers_of, item_id)).policy
            mine = await asyncio.to_thread(store.personal, user_id)
            shadowing = [
                n
                for n in held_names
                if policy.get(n) in (PRIVATE_FIRST, PRIVATE_ONLY) and n in mine
            ]
            if shadowing:
                found.append((user_id, item_id, shadowing))
        if body.apply:
            for user_id, item_id, shadowing in found:
                await asyncio.to_thread(store.drop_names, user_id, item_id, shadowing)
        return CleanupOut(
            applied=body.apply,
            rows=[CleanupRow(user_id=u, item_id=i, names=n) for u, i, n in found],
        )


def _sign_in_names(providers) -> frozenset[str]:  # noqa: ANN001 — IEnvProvider list
    """Every name some loaded provider produces. One that cannot say what it
    produces is skipped and named in the log — the same posture as the list of
    sign-in buttons — rather than failing the whole cleanup."""
    out: set[str] = set()
    for p in providers:
        try:
            out |= set(p.produces)
        except Exception:  # noqa: BLE001 — second-party code
            logger.warning(
                "env provider %s could not say what it produces; its names are not cleared",
                type(p).__name__,
                exc_info=True,
            )
    return frozenset(out)
