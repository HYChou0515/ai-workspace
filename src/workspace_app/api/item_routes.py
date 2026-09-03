"""App work-item lifecycle routes (#54).

Create an App's ``WorkItem`` (seeding its profile's files + collections) and close
one (the generic, manifest-driven lifecycle close that tears the sandbox down and,
when a chat pipeline is wired, promotes the dialogue to the insights KB).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Callable

import msgspec
from fastapi import APIRouter, FastAPI, HTTPException, Response, status
from pydantic import BaseModel
from specstar import SpecStar

from ..apps.base import WorkItemBase
from ..filestore.protocol import FileStore
from ..kb.ingest import Ingestor
from ..perm import Actor, Permission, Verb, authorize
from ..perm.model import user_subject
from ..quota.limits import parse_size
from ..resources.groups import groups_of
from ..sandbox.protocol import SandboxBusy
from .activity import ActivityLog
from .item_authz import require_item_access
from .item_conversation_perm import push_item_mirror_to_conversations
from .locator import ItemLocator
from .notifications import notification_sent, notify
from .permission_body import PermissionBody, PermissionOut, build_permission, granted_user_ids
from .promote import promote_chat_to_kb
from .registry import InvestigationRegistry
from .schemas import _CloseItemBody
from .turns import ChatTurnEngine

# asyncio holds only a WEAK reference to a bare ``create_task()`` result, so an
# un-referenced fire-and-forget task can be garbage-collected mid-flight — the
# background promote then vanishes before it writes the insight, surfacing as a
# flaky "no insight written" under GC pressure on a loaded CI runner. Keep a
# strong reference until each task finishes, discarding it on completion.
_promote_tasks: set[asyncio.Task[list[str]]] = set()

_LOGGER = logging.getLogger(__name__)


class ItemAccessRequestOut(BaseModel):
    """Result of POST /a/{slug}/items/{id}/request-access (permission-disclosure).
    ``requested`` is True iff a fresh owner notification was sent; ``already_readable``
    is True when the caller can already enter the workspace (nothing to request)."""

    item_id: str
    requested: bool
    already_readable: bool = False


class _MembersBody(BaseModel):
    members: list[str]


class _ResourcesBody(BaseModel):
    """How big this item's environment may be. Both dimensions optional and
    independent — an item may state memory and leave cpu to resolve, exactly as
    the config layer already lets an App do.

    ``None`` CLEARS: it means "nobody has said", which is not zero and not a
    number. Memory is a size string (``512M`` / ``2G``) so the wire matches
    what the operator writes in config, parsed by the one parser that already
    knows the spelling."""

    cpu_cores: float | None = None
    memory: str | None = None


class _EnvironmentOut(BaseModel):
    """This ONE item's environment — for whoever is in the workspace.

    Deliberately not `/me/resources`. That payload is scoped to a person and
    lists every environment they hold, with titles; a collaborator needs to know
    why THIS item was refused, not what else its owner is working on. So there
    is no total here and no other item, and the tests say so.

    `stated_*` is what somebody typed (``None`` = nobody has). `effective_*` is
    what will actually be applied, after the App's ceiling and the owner's
    budget have both had their say. They are reported separately because when
    they differ the UI has to explain WHICH one bound rather than silently
    showing the smaller number — a setting that quietly disagrees with what it
    does is the failure this whole design keeps circling."""

    running: bool
    stated_cpu_cores: float | None
    stated_memory_bytes: int | None
    effective_cpu_cores: float | None
    effective_memory_bytes: int | None
    #: What the BACKEND says it will really apply — `Sandbox.effective_limits`,
    #: the same source the quota ledger reads. #712's first defect was billing
    #: what was REQUESTED rather than what is applied, and an App that declared
    #: nothing occupied a core for free. Here the gap would be worse, because a
    #: PERSON set the number: they choose two cores, the panel shows two, and
    #: the sandbox runs uncapped on a deploy that cannot apply one.
    #:
    #: `None` means no ceiling will be applied — and deliberately does NOT
    #: distinguish "this backend caps nothing" from "we could not ask it".
    #: `HttpSandbox` reports an unreachable host identically to one that caps
    #: nothing (`warn_unenforceable_dimensions` says so in as many words), so a
    #: field that claimed to tell them apart would be inventing a distinction
    #: the backend cannot make. The UI says "cannot confirm" for both.
    enforced_cpu_cores: float | None
    enforced_memory_bytes: int | None


class _ResourcesOut(BaseModel):
    """What was stored. Bytes on the way out because that is what a
    ``SandboxSpec`` carries and a cgroup is written with; the string spelling
    belongs to the operator and the UI, not to the machinery."""

    cpu_cores: float | None
    memory_bytes: int | None


#: How far back a heartbeat still counts as "running". The same window the
#: admission gate uses, and for the same reason: a pod that died without
#: reaping stops counting on its own rather than pinning a limit forever.
_LIVE_WINDOW_MS = 120_000


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _validated_resources(body: _ResourcesBody) -> tuple[float | None, int | None]:
    """The two numbers, or a 422 naming which one and why.

    Only ``> 0`` is enforced, deliberately: there is no floor. Someone who sets
    a small environment chose it, and the connection between their setting and a
    slow environment is one they can make — inventing a minimum here would be a
    number nobody vouched for, and shipping it as an unset knob would be a
    protection that does not exist. What DOES have to hold up its end is the
    failure: a sandbox that cannot start at this size must say so against this
    setting rather than as a generic launch error.

    Zero is refused rather than treated as "unlimited". Elsewhere in this
    feature ``0`` means "no limit", but that reading belongs to an OPERATOR's
    config; here it would let a person silently opt their item out of a ceiling
    the deploy set for them."""
    cpu = body.cpu_cores
    if cpu is not None and cpu <= 0:
        raise HTTPException(
            status_code=422,
            detail=f"cpu_cores must be greater than 0 (got {cpu}); omit it to use the default",
        )
    memory: int | None = None
    if body.memory is not None:
        try:
            memory = parse_size(body.memory)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"memory: {exc}") from exc
        if memory <= 0:
            raise HTTPException(
                status_code=422,
                detail=f"memory must be greater than 0 (got {body.memory!r}); "
                "omit it to use the default",
            )
    return cpu, memory


# grill D7: a member is a Participant — the verbs that let them work in the item.
_PARTICIPANT_VERBS: tuple[Verb, ...] = ("read_meta", "read_chat", "read_content", "converse")


def _reconcile_member_grants(
    current: Permission | None, old_members: list[str], new_members: list[str]
) -> Permission:
    """grill D7 — fold the item's member roster into its ``Permission`` as Participant
    grants (read_meta + read_chat + read_content + converse), so a private-default
    item's members can actually enter it (and the storage-layer list scope, which
    reads the indexed ``permission.read_meta``, admits them). A member ADDED gains the
    participant verbs; a member REMOVED is stripped from them — any grant the owner
    made to a non-member (via the permission dialog) is untouched. Members exist ⇒
    ``restricted`` so the grants are live (``public`` stays public — open anyway)."""
    base = current if current is not None else Permission()
    old_subjects = {user_subject(m) for m in old_members}
    new_subjects = [user_subject(m) for m in new_members]
    removed = old_subjects - set(new_subjects)
    added = [s for s in new_subjects if s not in old_subjects]
    grants: dict[str, list[str]] = {}
    for verb in _PARTICIPANT_VERBS:
        kept = [s for s in base.grants(verb) if s not in removed]
        for s in added:
            if s not in kept:
                kept.append(s)
        grants[verb] = kept
    visibility = "restricted" if new_members and base.visibility != "public" else base.visibility
    return msgspec.structs.replace(base, visibility=visibility, **grants)


def register_item_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    filestore: FileStore,
    get_user_id: Callable[[], str],
    activity: ActivityLog,
    registry: InvestigationRegistry,
    turn_engine: ChatTurnEngine,
    locator: ItemLocator,
    ingestor: Ingestor,
    insights_collection_id: str,
    kb_chat_pipeline: object | None,
    superusers: frozenset[str] = frozenset(),
) -> None:
    """Mount the App work-item create / close routes onto ``app``."""

    def _authorize_item(slug: str, item_id: str, verb: Verb) -> tuple[WorkItemBase, str]:
        """#306 — gate a hand-written WorkItem route: ``read_meta`` first (404, no
        existence leak) then ``verb`` (403). Delegates to the shared
        ``require_item_access`` so item routes, file/chat routes, and stream all gate
        the SAME way (and honour ``group:`` grants — the old inline check ignored
        them). Returns the item + its owner."""
        return require_item_access(
            spec, slug, item_id, verb, user=get_user_id(), superusers=superusers
        )

    @app.post("/a/{slug}/items")
    async def create_app_item(slug: str, body: dict) -> dict:
        """#89 P4b — create an App's WorkItem + seed its profile's files. The
        body carries the item's fields; `owner` comes from auth and `profile`
        defaults to the App's `default_profile`."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.manifest import load_app_manifest
        from ..apps.registry import app_model
        from ..apps.seeding import case_from_item, seed_item

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        manifest = load_app_manifest(slug)
        model = app_model(slug)
        # `owner` comes from auth, never the body — and the environment sizes
        # go the same way, for a stronger reason: they are gated on
        # `change_permission` at their own route, and merging them from a create
        # body would let anyone who may create an item set one with no verb
        # checked and no `> 0` validation run. `memory 0` was the sharp end —
        # the cgroup reads it as `max` (unlimited) while admission charges
        # `memory_bytes or 0`, i.e. nothing.
        #
        # Dropped rather than refused: a client sending them is not doing
        # anything malicious, it is sending a field that has one correct place,
        # and the panel is right there once the item exists.
        payload = {
            k: v for k, v in body.items() if k not in ("sandbox_cpu_cores", "sandbox_memory_bytes")
        }
        payload["owner"] = get_user_id()
        payload.setdefault("profile", manifest.default_profile)
        # #306 PR3 (grill D6): NEW items default to PRIVATE (owner-only) — a
        # workspace is the creator's until they share it. The owner is `created_by`,
        # so authorize's owner-bypass keeps the creator's full access. Existing items
        # (no `permission`) stay public — absent ≡ public, no migration. A caller may
        # still pass an explicit `permission` to open it at create time.
        payload.setdefault("permission", {"visibility": "private"})
        try:
            item = msgspec.convert(payload, type=model)
        except msgspec.ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        rm = spec.get_resource_manager(model)
        # Seed the durable files BEFORE the WorkItem row exists, keyed on the id it
        # WILL have (pre-minted in specstar's own `{resource_name}:{uuid}` form).
        # Otherwise the item is discoverable — and its workspace warmable — while
        # its files are still being written one-by-one; a sandbox warm that lands in
        # that window restores a PARTIAL set and, because the facade serves any live
        # sandbox regardless of readiness, serves that partial set for as long as the
        # sandbox stays warm (the "PM item only has one file" data bug). Creating the
        # row LAST closes the window: durable is complete the instant the item
        # appears, so every warm restores the full set.
        item_id = f"{rm.resource_name}:{uuid.uuid4()}"
        # Seeding is BEST-EFFORT: if it raises, the item is still created (it just
        # starts emptier) rather than 500 and strand the user on a frozen modal. The
        # failure is logged (with the id) so an operator can see it.
        seeded: list[str] = []
        try:
            seeded = await seed_item(filestore, item_id, slug, item.profile, case_from_item(item))
            # #280: seed the item's collections.json from the profile's DEFAULT
            # collection set (declared by name + tier), resolving names → live ids. The
            # picker / Monaco then edit it. Unresolvable names are skipped; an
            # empty/undeclared default leaves whatever seed_item wrote alone.
            from ..apps.profiles import load_profile
            from ..kb.collections import resolve_profile_collections

            declared = [(c.name, c.tier) for c in load_profile(slug, item.profile).collections]
            rows = resolve_profile_collections(spec, declared)
            if rows:
                await filestore.write(
                    item_id, "/collections.json", json.dumps(rows, indent=2).encode()
                )
                seeded = sorted({*seeded, "/collections.json"})
        except Exception:  # noqa: BLE001 — best-effort seeding must not sink the create
            _LOGGER.exception(
                "create_app_item: pre-create seeding failed for %s (app=%s profile=%s) — "
                "creating the item anyway; it just starts emptier",
                item_id,
                slug,
                item.profile,
            )
        # Durable is now complete → create the row with the pre-minted id, making the
        # item discoverable (and warmable) ONLY after it is fully seeded.
        rev = rm.create(item, resource_id=item_id)
        try:
            activity.record("item_created", f"Created “{item.title}”", {"item_id": rev.resource_id})
        except Exception:  # noqa: BLE001 — the activity log is not worth failing a create over
            _LOGGER.exception("create_app_item: activity.record failed for %s", rev.resource_id)
        return {
            "resource_id": rev.resource_id,
            "app": slug,
            "profile": item.profile,
            "seeded": seeded,
        }

    @app.put("/a/{slug}/items/{item_id}/permission")
    async def set_item_permission(slug: str, item_id: str, body: PermissionBody) -> PermissionOut:
        """#306 — set an App item's access control (the FE share UI's backend).
        Only the owner / a superuser / a `change_permission` grantee may call it
        (404 if you can't see it, 403 if you can't change it). Mirrors the
        collection setter: persists AS THE OWNER (the per-verb write checker gates
        item updates on write_meta, which a change_permission-only delegate need
        not hold — and change_permission was just verified). Newly-granted users
        get a `share` notification."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.registry import app_model

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        model = app_model(slug)
        item, created_by = _authorize_item(slug, item_id, "change_permission")
        new_perm = build_permission(body)
        rm = spec.get_resource_manager(model)
        with rm.using(created_by):
            rm.update(item_id, msgspec.structs.replace(item, permission=new_perm))
        # The access gate holds an item's permission facts for a few seconds so
        # every request of a user action doesn't re-derive them. A revocation is
        # the one change that must not wait: drop it here so the very next
        # request re-reads. (The window bounds every OTHER path's staleness, so
        # this is about latency of intent, not correctness.)
        locator.forget_access(item_id)
        # #306 PR3: the item's read-visibility is denormalized onto its chats so the
        # Conversation auto-CRUD (which the item scope never covers) inherits the
        # change. Re-push ONLY when the fields the chat scope reads (visibility /
        # read_chat) actually moved. Item row is already persisted (404 immediately);
        # the per-chat loop runs OFF the loop but is AWAITED so shutdown can't strand it.
        old_perm = item.permission if item.permission is not None else Permission()
        if old_perm.visibility != new_perm.visibility or list(old_perm.read_chat) != list(
            new_perm.read_chat
        ):
            await asyncio.to_thread(
                push_item_mirror_to_conversations,
                spec,
                item_id,
                visibility=new_perm.visibility,
                read_chat=new_perm.read_chat,
                created_by=created_by,
            )
        me = get_user_id()
        notified = sorted(granted_user_ids(new_perm) - granted_user_ids(item.permission) - {me})
        for uid in notified:
            notify(
                spec,
                recipient=uid,
                kind="share",
                title=f'Shared an item: "{item.title}"',
                link=f"/a/{slug}/{item_id}",
                actor=me,
            )
        return PermissionOut(resource_id=item_id, visibility=new_perm.visibility, notified=notified)

    async def _environment_is_running(item_id: str) -> bool:
        """Whether something is RUNNING for this item — the heartbeat's answer.

        Deliberately not `registry.has_live_sandbox`. That one is the admission
        gate's question ("is this item already holding its slot") and on
        `kind: local`, with no address store, it degrades to "does the item's dir
        exist" — and those dirs live on a shared volume and outlive the
        processes until the idle reaper rmtrees them. Measured: with the pod's
        session gone and the dir still present it answers True, so a refusal
        built on it never lifts. The person is told to close an environment that
        is already gone, and the only way out is an eight-hour reaper.

        The heartbeat is the source the QUOTA bills from, so refusing an edit
        and charging for a sandbox now agree by construction: if nothing is
        being billed, there is nothing whose cgroup could disagree with a new
        number."""
        store = registry.activity
        if store is None:
            return await registry.has_live_sandbox(item_id)
        owner = locator.owner_of(item_id)
        if not owner:
            return False
        since = _now_ms() - _LIVE_WINDOW_MS
        live = await store.live_for(owner, since_ms=since)
        return any(s.item_id == item_id for s in live)

    @app.get("/a/{slug}/items/{item_id}/environment")
    async def get_item_environment(slug: str, item_id: str) -> _EnvironmentOut:
        """Is this item's environment running, how big is it, and who said so.

        Gated on ``read_chat`` — "may enter this workspace", which is where the
        panel lives. Higher than ``read_meta`` on purpose: that verb only puts a
        title in a dashboard list, so its holder has no screen for this and no
        use for the answer, and opening the route to them would be attack
        surface with no consumer. Aligning the gate with the screen it guards is
        also what stops the two drifting apart later.

        `running` comes from a real probe of THIS item, not from
        ``running_sandboxes()`` — that one answers for whichever replica took
        the request, so an item missing from it may simply be on another pod.
        It is safe to FIND things with and never safe to conclude absence from.
        """
        item, _created_by = _authorize_item(slug, item_id, "read_chat")
        effective = await registry.spec_for(item_id)
        enforced = await registry.sandbox.effective_limits(effective)
        return _EnvironmentOut(
            running=await registry.has_live_sandbox(item_id),
            stated_cpu_cores=getattr(item, "sandbox_cpu_cores", None),
            stated_memory_bytes=getattr(item, "sandbox_memory_bytes", None),
            effective_cpu_cores=effective.cpu_cores,
            effective_memory_bytes=effective.memory_bytes,
            enforced_cpu_cores=enforced.cpu_cores,
            enforced_memory_bytes=enforced.memory_bytes,
        )

    @app.put("/a/{slug}/items/{item_id}/resources")
    async def set_item_resources(slug: str, item_id: str, body: _ResourcesBody) -> _ResourcesOut:
        """How big THIS item's environment may be.

        Its own route rather than two more fields on the item PATCH, and the
        reason is the same one that pulled `members` out: this looks like a
        field and is actually a spending decision. It sets how much of the
        OWNER's budget the item may consume, so it is gated on
        ``change_permission`` — the only verb that is both semantically right
        and in ``AI_FORBIDDEN``, which is what stops the item's own agent (which
        runs inside that very sandbox) from raising its own ceiling.

        Folding it into the item PATCH would have meant a per-FIELD check inside
        a route that otherwise needs only ``write_meta`` — one rule in two
        places, and the kind that fails open: miss it and the PATCH still
        succeeds, with every existing test green.

        ``null`` in either dimension clears it, which is not the same as zero:
        cleared means "nobody has said", and the size resolves fresh from
        ``min(App ceiling, owner budget)`` every time it is asked.

        Persists AS THE OWNER for the same reason the permission setter does —
        a ``change_permission`` delegate need not hold ``write_meta``, and it is
        ``change_permission`` that was just verified."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.registry import app_model

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        model = app_model(slug)
        item, created_by = _authorize_item(slug, item_id, "change_permission")
        # Refused while the environment is LIVE, and this is a quota rule rather
        # than a UI nicety. There is no resize op — the size is applied when the
        # sandbox is created — but the heartbeat re-reads it on every bump, so
        # accepting a change now would re-bill the NEW number against a cgroup
        # still holding the old one. Lower it on a live item and the person is
        # charged 0.1 cores for 4 real ones, which repeated per item makes the
        # budget unbounded. Disabling the input alone left this door open.
        if await _environment_is_running(item_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This item's environment is running, so its size cannot change — "
                    "the running sandbox would keep the old one. Close the environment "
                    "first; the new size applies the next time it starts."
                ),
            )
        cpu, memory = _validated_resources(body)
        rm = spec.get_resource_manager(model)
        with rm.using(created_by):
            rm.update(
                item_id,
                msgspec.structs.replace(item, sandbox_cpu_cores=cpu, sandbox_memory_bytes=memory),
            )
        # The size is memoised for a few seconds with the item's other facts,
        # and the person who just saved it is the one who would meet the stale
        # copy. Same position `set_item_permission` takes on a revocation: the
        # write invalidates rather than the reader waiting it out.
        locator.forget_item(item_id)
        return _ResourcesOut(cpu_cores=cpu, memory_bytes=memory)

    @app.put("/a/{slug}/items/{item_id}/members")
    async def set_item_members(slug: str, item_id: str, body: _MembersBody) -> PermissionOut:
        """grill D7 — set an item's member roster AND sync it into the Permission as
        Participant grants (so a private-default item's members can enter it). Gated
        on ``change_permission`` (editing members now grants ACCESS, so it's owner /
        superuser / delegate only — no longer a plain ``write_meta`` field edit).
        Fans out the conversation read-chat mirror and notifies newly-added members."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.registry import app_model

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        model = app_model(slug)
        item, created_by = _authorize_item(slug, item_id, "change_permission")
        if not isinstance(item.members, list):
            raise HTTPException(status_code=422, detail=f"app {slug!r} has no members concept")
        old_members = item.members
        new_perm = _reconcile_member_grants(item.permission, old_members, body.members)
        rm = spec.get_resource_manager(model)
        with rm.using(created_by):
            rm.update(
                item_id, msgspec.structs.replace(item, members=body.members, permission=new_perm)
            )
        await asyncio.to_thread(
            push_item_mirror_to_conversations,
            spec,
            item_id,
            visibility=new_perm.visibility,
            read_chat=new_perm.read_chat,
            created_by=created_by,
        )
        me = get_user_id()
        added = sorted(set(body.members) - set(old_members) - {me})
        for uid in added:
            notify(
                spec,
                recipient=uid,
                kind="share",
                title=f'Added you to "{item.title}"',
                link=f"/a/{slug}/{item_id}",
                actor=me,
            )
        return PermissionOut(resource_id=item_id, visibility=new_perm.visibility, notified=added)

    @app.post("/a/{slug}/items/{item_id}/request-access")
    async def request_item_access(slug: str, item_id: str) -> ItemAccessRequestOut:
        """Permission-disclosure (grill D4): the caller (who can SEE the item via
        read_meta — the 🔒 locked list row) asks its owner to grant access. A
        ``read_meta`` gate first (404 for someone who can't discover it, no leak).
        Sends ONE deduped ``access_request`` notification to the owner; a caller who
        can already enter the workspace (``read_chat``) has nothing to request.
        Reuses the notify/bell + the owner's permission dialog — no durable request
        state. Mirrors the collection request-access endpoint."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.registry import app_model

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        rm = spec.get_resource_manager(app_model(slug))
        try:
            item = rm.get(item_id).data
        except Exception as exc:  # noqa: BLE001 — a missing id is a 404, not a 500
            raise HTTPException(status_code=404, detail="item not found") from exc
        assert isinstance(item, WorkItemBase)
        owner = rm.get_meta(item_id).created_by
        me = get_user_id()
        actor = Actor.human(me, groups=groups_of(spec, me))
        perm = item.permission
        # Already in the workspace (owner / superuser / can read_chat) → nothing to
        # request; precedes the read_meta 404 gate (read_chat need not imply an
        # explicit read_meta grant).
        if me == owner or authorize(
            actor, "read_chat", perm, created_by=owner, superusers=superusers
        ):
            return ItemAccessRequestOut(item_id=item_id, requested=False, already_readable=True)
        if not authorize(actor, "read_meta", perm, created_by=owner, superusers=superusers):
            raise HTTPException(status_code=404, detail="item not found")
        dedup_key = f"access_request:item:{item_id}:{me}"
        if notification_sent(spec, dedup_key):
            return ItemAccessRequestOut(item_id=item_id, requested=False)
        notify(
            spec,
            recipient=owner,
            kind="access_request",
            title=f'{me} requests access to "{item.title}"',
            body=(
                f'{me} asked to enter the workspace "{item.title}". Open its sharing '
                "settings to grant access."
            ),
            link=f"/a/{slug}/{item_id}",
            actor=me,
            dedup_key=dedup_key,
        )
        return ItemAccessRequestOut(item_id=item_id, requested=True)

    @app.post(
        "/a/{slug}/items/{item_id}/close",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def close_app_item(slug: str, item_id: str, body: _CloseItemBody) -> Response:
        """#89 P8 — generic, lifecycle-driven close for any App's WorkItem.
        A non-null `status` must be one of the manifest's
        `lifecycle.closing_states` and is set onto `lifecycle.status_field`;
        null leaves the item's status untouched. Either way the workspace
        session is torn down."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.manifest import load_app_manifest
        from ..apps.registry import app_model

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        manifest = load_app_manifest(slug)
        model = app_model(slug)
        rm = spec.get_resource_manager(model)
        # #306: closing is a lifecycle write — gate on write_meta (404 hides an
        # item the caller can't see, 403 blocks an in-scope member). Explicit here
        # because the pure-close path does no rm.update, so the write checker never
        # fires on it.
        current, _ = _authorize_item(slug, item_id, "write_meta")
        title = current.title
        if body.status is not None:
            lifecycle = manifest.lifecycle
            if lifecycle is None:  # pragma: no cover - every closable App declares lifecycle
                raise HTTPException(status_code=422, detail=f"app {slug!r} has no close lifecycle")
            if body.status not in lifecycle.closing_states:
                raise HTTPException(
                    status_code=422,
                    detail=f"{body.status!r} is not a closing state for app {slug!r}",
                )
            data = msgspec.to_builtins(current)
            data[lifecycle.status_field] = body.status
            rm.update(item_id, msgspec.convert(data, type=model))
            activity.record(
                "item_closed",
                f"Closed “{title}” as {body.status}",
                {"item_id": item_id},
            )
            # chat → knowledge: schedule insight extraction in the background so
            # the close response doesn't wait on the LLM. Only when a chat
            # pipeline is wired (LLM available).
            if kb_chat_pipeline is not None:
                _, conv_for_promote = locator.conversation_for(item_id)
                task = asyncio.create_task(
                    promote_chat_to_kb(
                        ingestor=ingestor,
                        insights_collection_id=insights_collection_id,
                        actor=get_user_id(),
                        investigation_id=item_id,
                        investigation_title=title,
                        messages=conv_for_promote.messages,
                    )
                )
                _promote_tasks.add(task)
                task.add_done_callback(_promote_tasks.discard)
            # Notify the owner + watchers (members are Tier-2 / opt-in), except
            # whoever did it.
            actor = get_user_id()
            members = current.members
            if isinstance(members, msgspec.UnsetType):  # pragma: no cover - RCA enables members
                members = []
            for uid in {current.owner, *members} - {actor}:
                notify(
                    spec,
                    recipient=uid,
                    kind="status",
                    title=f"{title} → {body.status}",
                    link=f"/a/{slug}/{item_id}",
                    actor=actor,
                )
        else:
            # Pure close — leave status untouched, just release the workspace.
            activity.record(
                "session_closed",
                f"Closed the workspace for “{title}”",
                {"item_id": item_id},
            )
        # Best effort, deliberately. By this point the status flip is persisted,
        # the activity recorded and the notifications fanned out — so failing
        # the request here reports "close failed" for work that is already done,
        # and a retry re-runs all of it (duplicate notifications, a second
        # promote task). A sandbox that could not be torn down right now is left
        # to the idle reaper, which is what would have collected it anyway.
        with contextlib.suppress(SandboxBusy):
            await registry.close_session(item_id)
        await turn_engine.forget(item_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
