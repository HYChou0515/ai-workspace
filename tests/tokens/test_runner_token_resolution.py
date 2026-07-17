"""The agent runner resolves each turn's api_key through the ITokenService when
the turn has a known user (``ctx.speaker``), and keeps the system default
otherwise — so a background job (no speaker) is unchanged, and a ``None`` key
(Ollama / no auth) stays ``None``.
"""

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.tokens import ITokenService
from workspace_app.users.protocol import User


class _RecordingService(ITokenService):
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def get_token(self, user_id: str) -> str:
        self.seen.append(user_id)
        return f"tok-{user_id}"


def _ctx(speaker: User | None) -> AgentToolContext:
    return AgentToolContext(speaker=speaker)


async def test_runner_uses_the_users_token_when_a_speaker_is_present():
    svc = _RecordingService()
    runner = LitellmAgentRunner(api_key="sys", token_service=svc)
    key = await runner._api_key_for(_ctx(User(id="alice", name="Alice")))
    assert key == "tok-alice"
    assert svc.seen == ["alice"]


async def test_runner_uses_the_system_key_when_there_is_no_speaker():
    # a background job / unauthed turn carries no speaker → system default, as before
    runner = LitellmAgentRunner(api_key="sys", token_service=_RecordingService())
    assert await runner._api_key_for(_ctx(None)) == "sys"


async def test_runner_defaults_to_a_system_token_service_from_the_api_key():
    # no explicit service injected: the behaviour-preserving default hands back the
    # system key even for a known user (v1 → external behaviour unchanged)
    runner = LitellmAgentRunner(api_key="sys")
    assert await runner._api_key_for(_ctx(User(id="bob", name="Bob"))) == "sys"


async def test_runner_preserves_a_none_api_key_even_with_a_speaker():
    # None key (Ollama / no auth) → stays None: there is no token to resolve
    runner = LitellmAgentRunner(api_key=None)
    assert await runner._api_key_for(_ctx(User(id="alice", name="Alice"))) is None
