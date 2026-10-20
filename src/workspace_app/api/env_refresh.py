"""Refresh a token before a tool gets it (`docs/plan-env-token-refresh.md`).

Runs between composing a person's layers (`private_layer` /
`unattended_layer`) and ``resolve``: for each sign-in (`IEnvProvider`) that
wrote a ``refresh``, it reads the values a tool is about to get for the names
that sign-in produces, asks the deploy whether they are near expiry (D2), and
if so swaps them for fresh ones and writes those back to the layer they came
from (D3).

The deploy's code is a black box; what it says is read ONLY through the
contract (D5): ``refresh`` returning is success, ``RefreshRejected(names)`` is
a dead refresh token — those names (and only ones it produces) are cleared and
the provider is reported so the person can be told (D4/D6) — and anything
else, a timeout included, is a passing failure that changes nothing.

Never blocks a run (D4): a holder that is slow costs at most ``wait_s``, a
deploy that hangs at most ``timeout_s``, and then the tool gets what it had.

The write-back is a PATCH, applied to what the layer holds when it is
written: the refreshed names are set (or the dead ones cleared) only if they
still hold what was sent. Anything else in the layer — an edit made while the
deploy was refreshing — is left as it now is, and a set someone changed or
removed meanwhile (signed out, signed in again) is theirs, not overwritten.

Only one layer may be written back to: a set of values that comes from several
layers, from the seam (the deploy's ``IRequestEnv``, rewritten on every
request) or from the service account has no row of ours to hold a refreshed
one, so it is left as it is.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from specstar import SpecStar

from .env_layers import PersonEnv, is_blank, layer_of
from .env_provider import IEnvProvider, RefreshRejected, supports_refresh
from .locator import ItemLocator
from .notifications import notification_sent, notify
from .private_env import PrivateEnvStore, own_layer
from .refresh_claims import RefreshClaims

logger = logging.getLogger(__name__)

Writable = Literal["personal", "private", "shared"]
#: A write-back: what the layer becomes, given what it holds now — None
#: leaves it as it is.
Patch = Callable[[dict[str, str]], "dict[str, str] | None"]

#: The platform's own bound on a deploy's ``refresh`` — the contract asks the
#: deploy to bound its latency, but a hung one must not hang every turn.
REFRESH_TIMEOUT_S = 30.0
#: How long a run waits for another run's refresh of the same values.
WAIT_FOR_PEER_S = 30.0
#: A claim outlives the refresh it guards (timeout plus margin), and no more.
CLAIM_TTL_MS = int((REFRESH_TIMEOUT_S + 15) * 1000)
_POLL_S = 0.05


@dataclass(frozen=True)
class DeadSignIn:
    """A sign-in whose refresh token the deploy reported dead (D5), and the
    layer it was read from — where signing in again must write (D3)."""

    provider: IEnvProvider
    layer: Writable


@dataclass(frozen=True)
class RefreshOutcome:
    person: PersonEnv
    shared: dict[str, str]
    #: Sign-ins whose refresh token the deploy reported dead (D5) — the person
    #: is told to sign in again (D4 / D6).
    rejected: list[DeadSignIn] = field(default_factory=list)


async def refresh_if_due(
    *,
    providers: Sequence[IEnvProvider],
    store: PrivateEnvStore | None,
    claims: RefreshClaims | None,
    read_shared: Callable[[str], dict[str, str]],
    update_shared: Callable[[str, Patch], dict[str, str]],
    shared: dict[str, str],
    person: PersonEnv,
    policy: dict[str, str],
    user_id: str,
    item_id: str,
    timeout_s: float = REFRESH_TIMEOUT_S,
    wait_s: float = WAIT_FOR_PEER_S,
) -> RefreshOutcome:
    out = RefreshOutcome(person=person, shared=dict(shared))
    if store is None or claims is None:
        return out
    for provider in providers:
        if not supports_refresh(provider):
            continue
        out = await _one(
            provider,
            out,
            store=store,
            claims=claims,
            read_shared=read_shared,
            update_shared=update_shared,
            policy=policy,
            user_id=user_id,
            item_id=item_id,
            timeout_s=timeout_s,
            wait_s=wait_s,
        )
    return out


def _source(
    provider: IEnvProvider, out: RefreshOutcome, policy: dict[str, str]
) -> tuple[dict[str, str], set[str]]:
    """The values a tool would get for this provider's names now, and the
    layers they come from."""
    p = out.person
    layers = {"shared": out.shared, "private": p.own, "personal": p.personal, "service": p.service}
    values: dict[str, str] = {}
    sources: set[str] = set()
    for name in sorted(provider.produces):
        layer = layer_of(
            name,
            shared=out.shared,
            private=p.own,
            personal=p.personal,
            service=p.service,
            policy=policy,
        )
        if layer != "none":
            values[name] = layers[layer][name]
            sources.add(layer)
    return values, sources


def _due(provider: IEnvProvider, values: dict[str, str]) -> bool:
    try:
        return bool(provider.needs_refresh(values))
    except Exception:  # noqa: BLE001 — a deploy's bug must not stop the run; said so
        logger.warning("env-refresh: %s.needs_refresh raised; treated as not due", provider.id)
        return False


async def _one(
    provider: IEnvProvider,
    out: RefreshOutcome,
    *,
    store: PrivateEnvStore,
    claims: RefreshClaims,
    read_shared: Callable[[str], dict[str, str]],
    update_shared: Callable[[str, Patch], dict[str, str]],
    policy: dict[str, str],
    user_id: str,
    item_id: str,
    timeout_s: float,
    wait_s: float,
) -> RefreshOutcome:
    values, sources = _source(provider, out, policy)
    if not values or len(sources) != 1:
        return out  # nothing to refresh, or no single layer to write it back to
    target = next(iter(sources))
    if target == "service":
        return out
    seam: dict[str, str] = {}
    if target == "private":
        seam = await asyncio.to_thread(store.seam, user_id, item_id)
        if any(not is_blank(seam.get(n, "")) for n in values):
            return out  # the seam's, rewritten on every request: not ours to refresh
    if not _due(provider, values):
        return out
    layer: Writable = target  # ty: ignore[invalid-assignment]
    owner = {"personal": user_id, "private": f"{user_id}:{item_id}", "shared": item_id}[layer]
    key = f"{layer}:{owner}:{provider.id}"

    def read() -> dict[str, str]:
        if layer == "personal":
            return store.personal(user_id)
        if layer == "private":
            return store.get(user_id, item_id)
        return read_shared(item_id)

    def write(patch: Patch) -> tuple[dict[str, str], bool]:
        """The layer after ``patch``, applied to what it holds NOW (for the
        shared copy inside the item's CAS), and whether it was written."""
        if layer == "shared":
            applied: list[bool] = []

            def tracked(now: dict[str, str]) -> dict[str, str] | None:
                new = patch(now)
                applied[:] = [new is not None]
                return new

            return update_shared(item_id, tracked), applied == [True]
        now = read()
        new = patch(now)
        if new is None:
            return now, False
        if layer == "personal":
            store.replace_personal(user_id, new)
        else:
            store.replace(user_id, item_id, new)
        return new, True

    holder = await asyncio.to_thread(claims.try_take, key, ttl_ms=CLAIM_TTL_MS)
    if holder is None:
        return await _wait_for_peer(
            out, provider, layer, read, lambda: claims.held(key), values, wait_s, seam
        )
    try:
        # Read again under the claim: a peer may have refreshed (and, on a
        # rotating system, spent the refresh token we read) since.
        current = await asyncio.to_thread(read)
        stored = {n: current[n] for n in values if n in current}
        if stored != values and not _due(provider, stored):
            return _with(out, layer, current, seam)  # a peer refreshed it just now
        try:
            fresh = await asyncio.wait_for(provider.refresh(dict(stored)), timeout_s)
        except RefreshRejected as rejected:
            dead = rejected.names & provider.produces
            kept, cleared = await asyncio.to_thread(
                write,
                _unchanged(stored, lambda now: {n: v for n, v in now.items() if n not in dead}),
            )
            done = _with(out, layer, kept, seam)
            if not cleared:
                return done  # signed in again meanwhile: those are live
            logger.info(
                "env-refresh: %s rejected for %s; cleared %s", provider.id, layer, sorted(dead)
            )
            return replace(done, rejected=[*done.rejected, DeadSignIn(provider, layer)])
        except Exception:  # noqa: BLE001 — D5: anything else is a passing failure
            logger.warning("env-refresh: %s refresh failed; tool keeps its values", provider.id)
            return out
        merged, wrote = await asyncio.to_thread(
            write, _unchanged(stored, lambda now: {**now, **fresh})
        )
        if wrote:
            logger.info("env-refresh: %s refreshed %s for %s", provider.id, sorted(fresh), layer)
        return _with(out, layer, merged, seam)
    finally:
        await asyncio.to_thread(claims.release, key, holder)


def _unchanged(stored: dict[str, str], change: Callable[[dict[str, str]], dict[str, str]]) -> Patch:
    """``change``, only while the names refreshed still hold what was sent."""

    def patch(now: dict[str, str]) -> dict[str, str] | None:
        if {n: now[n] for n in stored if n in now} != stored:
            return None
        return change(now)

    return patch


async def _wait_for_peer(
    out: RefreshOutcome,
    provider: IEnvProvider,
    layer: Writable,
    read: Callable[[], dict[str, str]],
    held: Callable[[], bool],
    values: dict[str, str],
    wait_s: float,
    seam: dict[str, str],
) -> RefreshOutcome:
    """Someone else is refreshing these values: wait for what they write, or
    go on with what we had (D4) — as soon as they let go of the claim, not
    only when the wait runs out. Names they cleared mean they found the
    sign-in dead, so this run reports it too."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while loop.time() < deadline:
        await asyncio.sleep(_POLL_S)
        current = await asyncio.to_thread(read)
        if {n: current.get(n) for n in values} != values:
            done = _with(out, layer, current, seam)
            if all(is_blank(current.get(n, "")) for n in values):
                return replace(done, rejected=[*done.rejected, DeadSignIn(provider, layer)])
            return done
        if not await asyncio.to_thread(held):
            return out  # they gave up without writing: a passing failure
    return out


def _with(
    out: RefreshOutcome, layer: Writable, values: dict[str, str], seam: dict[str, str]
) -> RefreshOutcome:
    """The outcome with ``layer`` replaced by what is stored there now."""
    if layer == "shared":
        return replace(out, shared=dict(values))
    if layer == "personal":
        return replace(out, person=replace(out.person, personal=dict(values)))
    # private: composed exactly as the turn composes it — what they typed, with
    # the seam's answer over it — so a cleared name is gone from the tool's env.
    return replace(out, person=replace(out.person, own=own_layer(values, seam)))


#: Who a refreshed shared copy is written as (D3): the platform, not a person.
REFRESH_ACTOR = "system:env-refresh"


class EnvRefresher:
    """``refresh_if_due`` with the app's own parts — the ONE call every entry
    point that runs a tool for a person makes, between composing the person's
    layers and ``resolve`` (chat send, a page's tool call, a goal round, a
    re-run, a workflow node).

    The sign-ins are read at CALL time (``providers()``), the same seam the
    env-provider routes read, so a deploy reloading them — or a test swapping
    them — is seen. A deploy with no sign-in that refreshes costs nothing: no
    item read, no I/O."""

    def __init__(
        self,
        *,
        providers: Callable[[], Sequence[IEnvProvider]],
        store: PrivateEnvStore | None,
        claims: RefreshClaims | None,
        locator: ItemLocator,
        spec: SpecStar | None = None,
    ) -> None:
        self._providers = providers
        self._store = store
        self._claims = claims
        self._locator = locator
        #: Where a notice to someone not present is written (D4); None ⇒ none.
        self._spec = spec

    async def apply(
        self, person: PersonEnv, *, user_id: str, item_id: str, in_chat: bool
    ) -> RefreshOutcome:
        """``in_chat``: the person is in the chat thread the run answers (a
        chat send) — a dead sign-in is returned for the thread to show (D6).
        Otherwise (a page's tool call, which has no thread; a goal round, a
        re-run, a workflow node, which nobody is watching) the person the run
        is for is notified here (D4)."""
        providers = [p for p in self._providers() if supports_refresh(p)]
        if not providers:
            return RefreshOutcome(person=person, shared={})
        layers = await asyncio.to_thread(self._locator.env_layers_of, item_id)
        outcome = await refresh_if_due(
            providers=providers,
            store=self._store,
            claims=self._claims,
            read_shared=lambda i: self._locator.env_layers_of(i).shared,
            update_shared=lambda i, p: self._locator.update_shared_env(i, p, actor=REFRESH_ACTOR),
            shared=layers.shared,
            person=person,
            policy=layers.policy,
            user_id=user_id,
            item_id=item_id,
        )
        if not in_chat and user_id and self._spec is not None:
            for dead in outcome.rejected:
                slug = None
                if dead.layer != "personal":
                    slug = await asyncio.to_thread(self._locator.slug_of, item_id)
                await asyncio.to_thread(
                    _notify_dead, self._spec, user_id, dead, item_id=item_id, slug=slug
                )
        return outcome


def _notify_dead(
    spec: SpecStar, recipient: str, dead: DeadSignIn, *, item_id: str, slug: str | None
) -> None:
    """Tell someone not present that a sign-in must be done again (D4) — once
    a day per person and sign-in (and item, for an item's own copy), however
    many runs find it. It leads to where the dead token lived (D3): My
    environment variables, or the item whose Private / shared copy held it.
    The names and values stay out of it: what died is said by the sign-in's
    label."""
    label = dead.provider.label
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if dead.layer == "personal" or slug is None:
        where, link = "under My environment variables", "/my-env"
        key = f"env-refresh-dead:{recipient}:{dead.provider.id}:{day}"
    else:
        where, link = "from the item's environment variables", f"/a/{slug}/{item_id}"
        key = f"env-refresh-dead:{recipient}:{dead.provider.id}:{item_id}:{day}"
    if notification_sent(spec, key):
        return
    notify(
        spec,
        recipient=recipient,
        kind="env_sign_in_expired",
        title=f"Sign in to {label} again",
        body=(
            f"The saved sign-in to {label} has expired and could not be renewed. "
            f"Sign in again {where}."
        ),
        link=link,
        dedup_key=key,
    )
