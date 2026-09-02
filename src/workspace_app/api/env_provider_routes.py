"""#750 — the routes behind "log in, get the variables".

``GET  …/env-providers``            what this deploy can fill, and what it asks for
``POST …/env-providers/{id}``       run one exchange and hand back the variables

Neither route writes anything. The second returns what the exchange produced so
the panel can put it in the form; the person still presses Save. Writing here
would give one dialog two save semantics with nothing on screen to tell them
apart — some actions taking effect on click, others on Save.

The credential arrives, reaches the deploy's own implementation, and stops. It
is not stored, not logged, and not echoed back.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel
from specstar import SpecStar

from .env_provider import IEnvProvider
from .item_authz import require_item_access

logger = logging.getLogger(__name__)


class InputFieldOut(BaseModel):
    name: str
    label: str
    secret: bool = False


class EnvProviderOut(BaseModel):
    """One offer the panel can draw a button for."""

    id: str
    label: str
    produces: list[str]
    """The variable names it fills. The panel matches these against what the
    item's tools declared — the only join between a tool and a provider."""
    inputs: list[InputFieldOut]


class EnvProviders(BaseModel):
    providers: list[EnvProviderOut]


class ResolveBody(BaseModel):
    values: dict[str, str]
    """What the person typed. Holds the credential; never persisted."""


class ResolvedEnv(BaseModel):
    env: dict[str, str]
    """Everything the provider returned, unfiltered — including names no tool
    declared. Filtering to declared names would drop exactly what an incomplete
    declaration most needs to keep."""


def register_env_provider_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    get_user_id,
    superusers: frozenset[str] = frozenset(),
) -> None:
    def _providers(request: Request) -> list[IEnvProvider]:
        # Read off app state rather than closed over, so a test (and a deploy
        # reloading config) can swap the list without rebuilding the app.
        return list(getattr(request.app.state, "env_providers", ()) or ())

    def _gate(slug: str, item_id: str) -> None:
        """Same verb as storing a variable by hand: ``write_meta``.

        The exchange mints a credential's product for THIS item, and the whole
        point is to put it in the panel — so anyone who could not save it by
        hand must not be able to mint it either. Gating only the eventual save
        would leave a reader able to trigger a login and read the token out of
        the response."""
        require_item_access(
            spec, slug, item_id, "write_meta", user=get_user_id(), superusers=superusers
        )

    def _describe(provider: IEnvProvider) -> EnvProviderOut | None:
        """One offer, or ``None`` when the implementation could not describe
        itself.

        Every line here runs SECOND-PARTY code — four of its properties — and
        an exception in any one would otherwise fail the whole response. The
        panel reads that as "no providers" and draws no buttons at all, so a
        deploy with three working logins would lose all three to a typo in a
        fourth, with nothing on screen and nothing in the answer to say why.
        Same posture as ``discover_packages``: degrade, name the offender, and
        let everything that still works keep working."""
        try:
            return EnvProviderOut(
                id=provider.id,
                label=provider.label,
                produces=sorted(provider.produces),
                inputs=[
                    InputFieldOut(name=f.name, label=f.label, secret=f.secret)
                    for f in provider.inputs
                ],
            )
        except Exception:  # noqa: BLE001 — any failure costs this one button
            # Named by its ID first: that is the string an operator can find in
            # their own config, whereas a class name means nothing until they go
            # looking for it. Reading the id can itself be the thing that failed,
            # so it falls back to the class rather than raising inside the
            # handler for a raise.
            try:
                who = provider.id
            except Exception:  # noqa: BLE001 — the id is what broke
                who = type(provider).__name__
            logger.warning(
                "env provider %r could not describe itself; its button is not offered",
                who,
                exc_info=True,
            )
            return None

    def _find(providers: list[IEnvProvider], wanted: str) -> IEnvProvider | None:
        """The provider with this id, skipping any that cannot say their own.

        The obvious `next(p for p in … if p.id == wanted)` reads `.id` on every
        implementation until it matches — so one whose `id` raises would fail
        the exchange for a DIFFERENT, working button. The list route already
        hides a broken provider, which makes that worse rather than better: the
        good buttons are all still on screen, and pressing one reports the
        failure against the login the person pressed.
        """
        for p in providers:
            try:
                if p.id == wanted:
                    return p
            except Exception:  # noqa: BLE001 — it cannot be the one we want
                logger.warning(
                    "env provider %s raised while being asked for its id; skipped",
                    type(p).__name__,
                    exc_info=True,
                )
        return None

    @app.get("/a/{slug}/items/{item_id}/env-providers")
    async def list_env_providers(slug: str, item_id: str, request: Request) -> EnvProviders:
        _gate(slug, item_id)
        described = (_describe(p) for p in _providers(request))
        return EnvProviders(providers=[d for d in described if d is not None])

    @app.post("/a/{slug}/items/{item_id}/env-providers/{provider_id}")
    async def resolve_env_provider(
        slug: str, item_id: str, provider_id: str, body: ResolveBody, request: Request
    ) -> ResolvedEnv:
        _gate(slug, item_id)
        provider = _find(_providers(request), provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"unknown env provider: {provider_id!r}")
        try:
            env = await provider.resolve(dict(body.values))
        except Exception as exc:  # noqa: BLE001 — any failure is reported the same way
            # 400, deliberately NOT a 5xx gateway status: the FE reads
            # 502/503/504 as "an idle proxy cut the connection while the work
            # goes on" and waits for a result that will never arrive (#714).
            # A wrong password has to land as something the dialog will show.
            #
            # The message is the exception's TYPE and text as the implementation
            # wrote it — never `values`, which holds the credential.
            logger.warning("env provider %r failed for item %s: %s", provider_id, item_id, exc)
            raise HTTPException(
                status_code=400,
                detail={"error": "env_provider_failed", "provider": provider_id, "why": str(exc)},
            ) from exc
        return ResolvedEnv(env=dict(env))
