"""The agent runner resolves EACH per-turn endpoint credential (the primary
``config.llm_api_key`` and every fallback endpoint's key) through the ITokenService
on the acting user's behalf, telling it which lane the call is on. A turn that knows
nobody's behalf it runs on → identity, so an unattributed turn is byte-for-byte
unchanged.
"""

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.resources import AgentConfig
from workspace_app.tokens import CallLane, ITokenService, LlmCredential, PassthroughTokenService
from workspace_app.users.protocol import User


def _endpoint(model, base_url, api_key):
    from workspace_app.factories import LlmEndpoint

    return LlmEndpoint(
        model=model,
        base_url=base_url,
        api_key=api_key,
        reasoning_effort=None,
        ttft_s=0.0,
        idle_s=0.0,
        cooldown_s=0.0,
    )


class _EndpointKeyService(ITokenService):
    """Records the (user, key, lane) triples it is asked to resolve and maps each to
    a user-scoped credential, so we can see WHICH endpoint keys got routed through
    it and on which lane."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str | None, str]] = []

    async def get_credential(
        self, user_id: str, current_key: str | None, lane: CallLane
    ) -> LlmCredential:
        self.seen.append((user_id, current_key, lane))
        return LlmCredential(f"{user_id}:{current_key}", {"X-Lane": lane})


def _ctx(speaker: User | None, config: AgentConfig | None, **kw) -> AgentToolContext:
    return AgentToolContext(speaker=speaker, agent_config=config, **kw)


async def test_resolver_routes_the_primary_endpoint_key_for_the_speaker():
    svc = _EndpointKeyService()
    runner = LitellmAgentRunner(api_key="runner-default", token_service=svc)
    config = AgentConfig(name="p", model="m", llm_api_key="preset-key")
    resolve = await runner._credential_resolver(
        _ctx(User(id="alice", name="A"), config, call_lane="interactive")
    )
    assert resolve("preset-key").api_key == "alice:preset-key"
    assert ("alice", "preset-key", "interactive") in svc.seen


async def test_the_credentials_headers_are_what_reaches_the_call():
    # The widened seam: whatever headers the source returns (a session cookie, a
    # lane tag for the gateway's rate limiter) come back with the key.
    svc = _EndpointKeyService()
    runner = LitellmAgentRunner(token_service=svc)
    config = AgentConfig(name="p", model="m", llm_api_key="preset-key")
    resolve = await runner._credential_resolver(
        _ctx(User(id="alice", name="A"), config, call_lane="interactive")
    )
    assert resolve("preset-key").headers == {"X-Lane": "interactive"}


async def test_resolver_routes_every_fallback_endpoint_key_too():
    svc = _EndpointKeyService()
    chains = {
        ("m", None): [
            _endpoint("m", None, "fb-key-1"),
            _endpoint("m2", "http://x", "fb-key-2"),
        ]
    }
    runner = LitellmAgentRunner(token_service=svc, fallback_chains=chains)
    config = AgentConfig(name="p", model="m", llm_api_key="primary-key")
    resolve = await runner._credential_resolver(_ctx(User(id="bob", name="B"), config))
    assert resolve("primary-key").api_key == "bob:primary-key"
    assert resolve("fb-key-1").api_key == "bob:fb-key-1"
    assert resolve("fb-key-2").api_key == "bob:fb-key-2"


async def test_a_background_turn_resolves_on_the_acting_users_behalf():
    # A workflow step / card-gen pass has no speaker (nobody is watching) but it
    # still runs on someone's behalf, and the gateway still has to know whose quota
    # to charge — so `acting_user` stands in for the speaker.
    svc = _EndpointKeyService()
    runner = LitellmAgentRunner(token_service=svc)
    config = AgentConfig(name="p", model="m", llm_api_key="preset-key")
    resolve = await runner._credential_resolver(_ctx(None, config, acting_user="carol"))
    assert resolve("preset-key").api_key == "carol:preset-key"
    assert ("carol", "preset-key", "background") in svc.seen


async def test_the_lane_defaults_to_background():
    # Fail-safe: a turn nobody labelled gets the TIGHTER quota. The reverse — an
    # unlabelled batch job spending the interactive allowance — is the thing the
    # lane exists to prevent.
    svc = _EndpointKeyService()
    runner = LitellmAgentRunner(token_service=svc)
    config = AgentConfig(name="p", model="m", llm_api_key="preset-key")
    assert AgentToolContext().call_lane == "background"
    resolve = await runner._credential_resolver(_ctx(User(id="alice", name="A"), config))
    assert resolve("preset-key").headers == {"X-Lane": "background"}


async def test_resolver_is_identity_when_the_turn_runs_on_nobodys_behalf():
    # no speaker AND no acting user → nothing to resolve, every key passes through
    runner = LitellmAgentRunner(api_key="k", token_service=_EndpointKeyService())
    config = AgentConfig(name="p", model="m", llm_api_key="preset-key")
    resolve = await runner._credential_resolver(_ctx(None, config))
    assert resolve("preset-key") == LlmCredential("preset-key")
    assert resolve(None) == LlmCredential(None)


async def test_resolver_is_identity_with_the_passthrough_service():
    # v1 default: PassthroughTokenService → every endpoint keeps its own key, and
    # no headers are added, so the default deploy sends exactly what it sent before
    runner = LitellmAgentRunner(token_service=PassthroughTokenService())
    config = AgentConfig(name="p", model="m", llm_api_key="preset-key")
    resolve = await runner._credential_resolver(_ctx(User(id="alice", name="A"), config))
    assert resolve("preset-key") == LlmCredential("preset-key")


async def test_resolver_is_identity_when_no_service_is_wired():
    runner = LitellmAgentRunner(api_key="k")  # token_service=None
    config = AgentConfig(name="p", model="m", llm_api_key="preset-key")
    resolve = await runner._credential_resolver(_ctx(User(id="alice", name="A"), config))
    assert resolve("preset-key") == LlmCredential("preset-key")
