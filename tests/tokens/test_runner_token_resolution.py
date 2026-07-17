"""The agent runner resolves EACH per-turn endpoint key (the primary
``config.llm_api_key`` and every fallback endpoint's key) through the
ITokenService on the acting user's behalf. No speaker / no service → identity, so
a user-less turn (background job) is byte-for-byte unchanged.
"""

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.resources import AgentConfig
from workspace_app.tokens import ITokenService, PassthroughTokenService
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
    """Records the (user, key) pairs it is asked to resolve and maps each to a
    user-scoped token, so we can see WHICH endpoint keys got routed through it."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str | None]] = []

    async def get_token(self, user_id: str, current_key: str | None) -> str | None:
        self.seen.append((user_id, current_key))
        return f"{user_id}:{current_key}"


def _ctx(speaker: User | None, config: AgentConfig | None) -> AgentToolContext:
    return AgentToolContext(speaker=speaker, agent_config=config)


async def test_resolver_routes_the_primary_endpoint_key_for_the_speaker():
    svc = _EndpointKeyService()
    runner = LitellmAgentRunner(api_key="runner-default", token_service=svc)
    config = AgentConfig(name="p", model="m", llm_api_key="preset-key")
    resolve = await runner._key_resolver(_ctx(User(id="alice", name="A"), config))
    assert resolve("preset-key") == "alice:preset-key"
    assert ("alice", "preset-key") in svc.seen


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
    resolve = await runner._key_resolver(_ctx(User(id="bob", name="B"), config))
    assert resolve("primary-key") == "bob:primary-key"
    assert resolve("fb-key-1") == "bob:fb-key-1"
    assert resolve("fb-key-2") == "bob:fb-key-2"


async def test_resolver_is_identity_when_there_is_no_speaker():
    # background job / unauthed → no user → every key passes through unchanged
    runner = LitellmAgentRunner(api_key="k", token_service=_EndpointKeyService())
    config = AgentConfig(name="p", model="m", llm_api_key="preset-key")
    resolve = await runner._key_resolver(_ctx(None, config))
    assert resolve("preset-key") == "preset-key"
    assert resolve(None) is None


async def test_resolver_is_identity_with_the_passthrough_service():
    # v1 default: PassthroughTokenService → every endpoint keeps its own key
    runner = LitellmAgentRunner(token_service=PassthroughTokenService())
    config = AgentConfig(name="p", model="m", llm_api_key="preset-key")
    resolve = await runner._key_resolver(_ctx(User(id="alice", name="A"), config))
    assert resolve("preset-key") == "preset-key"


async def test_resolver_is_identity_when_no_service_is_wired():
    runner = LitellmAgentRunner(api_key="k")  # token_service=None
    config = AgentConfig(name="p", model="m", llm_api_key="preset-key")
    resolve = await runner._key_resolver(_ctx(User(id="alice", name="A"), config))
    assert resolve("preset-key") == "preset-key"
