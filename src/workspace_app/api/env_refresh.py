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
    write_shared: Callable[[str, dict[str, str]], None],
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
            write_shared=write_shared,
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
    write_shared: Callable[[str, dict[str, str]], None],
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

    def write(new_layer: dict[str, str]) -> None:
        if layer == "personal":
            store.replace_personal(user_id, new_layer)
        elif layer == "private":
            store.replace(user_id, item_id, new_layer)
        else:
            write_shared(item_id, new_layer)

    holder = await asyncio.to_thread(claims.try_take, key, ttl_ms=CLAIM_TTL_MS)
    if holder is None:
        return await _wait_for_peer(out, layer, read, values, wait_s, seam)
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
            kept = {n: v for n, v in current.items() if n not in dead}
            await asyncio.to_thread(write, kept)
            logger.info(
                "env-refresh: %s rejected for %s; cleared %s", provider.id, layer, sorted(dead)
            )
            done = _with(out, layer, kept, seam)
            return replace(done, rejected=[*done.rejected, DeadSignIn(provider, layer)])
        except Exception:  # noqa: BLE001 — D5: anything else is a passing failure
            logger.warning("env-refresh: %s refresh failed; tool keeps its values", provider.id)
            return out
        merged = {**current, **fresh}
        await asyncio.to_thread(write, merged)
        logger.info("env-refresh: %s refreshed %s for %s", provider.id, sorted(fresh), layer)
        return _with(out, layer, merged, seam)
    finally:
        await asyncio.to_thread(claims.release, key, holder)


async def _wait_for_peer(
    out: RefreshOutcome,
    layer: Writable,
    read: Callable[[], dict[str, str]],
    values: dict[str, str],
    wait_s: float,
    seam: dict[str, str],
) -> RefreshOutcome:
    """Someone else is refreshing these values: wait for what they write, or
    go on with what we had (D4)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while loop.time() < deadline:
        await asyncio.sleep(_POLL_S)
        current = await asyncio.to_thread(read)
        if {n: current.get(n) for n in values} != values:
            return _with(out, layer, current, seam)
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
        self, person: PersonEnv, *, user_id: str, item_id: str, present: bool
    ) -> RefreshOutcome:
        """``present``: someone is at the request (a chat send, a page's tool
        call) — a dead sign-in is returned for the surface to show (D6).
        Otherwise (a goal round, a re-run, a workflow node) the person the run
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
            write_shared=lambda i, v: self._locator.write_shared_env(i, v, actor=REFRESH_ACTOR),
            shared=layers.shared,
            person=person,
            policy=layers.policy,
            user_id=user_id,
            item_id=item_id,
        )
        if not present and user_id and self._spec is not None:
            for dead in outcome.rejected:
                await asyncio.to_thread(_notify_dead, self._spec, user_id, dead.provider)
        return outcome


def _notify_dead(spec: SpecStar, recipient: str, provider: IEnvProvider) -> None:
    """Tell someone not present that a sign-in of theirs must be done again
    (D4) — once a day per person and sign-in, however many runs find it. The
    names and values stay out of it: what died is said by the sign-in's label."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    key = f"env-refresh-dead:{recipient}:{provider.id}:{day}"
    if notification_sent(spec, key):
        return
    notify(
        spec,
        recipient=recipient,
        kind="env_sign_in_expired",
        title=f"Sign in to {provider.label} again",
        body=(
            f"Your saved sign-in to {provider.label} has expired and could not be renewed. "
            "Sign in again under My environment variables."
        ),
        link="/my-env",
        dedup_key=key,
    )
