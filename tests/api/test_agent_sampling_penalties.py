"""#113 Layer 1: per-config sampling penalties reach the model's ModelSettings.

A cheap first line of defence against repetition loops (works on vLLM; Ollama's
Go runner silently drops them — which is why the stream guard, not this, is the
real backstop). Unset penalties must leave ModelSettings at the model default.
"""

from workspace_app.api.litellm_runner import _agent_for
from workspace_app.resources.agent_config import AgentConfig


def test_penalties_are_injected_into_model_settings():
    agent = _agent_for(
        AgentConfig(
            name="a",
            frequency_penalty=0.3,
            presence_penalty=0.2,
            repetition_penalty=1.1,
        )
    )
    ms = agent.model_settings
    assert ms.frequency_penalty == 0.3
    assert ms.presence_penalty == 0.2
    # repetition_penalty is non-standard → extra_body (forwarded by litellm).
    assert (ms.extra_body or {}).get("repetition_penalty") == 1.1  # ty: ignore[unresolved-attribute]


def test_unset_penalties_leave_model_settings_at_default():
    ms = _agent_for(AgentConfig(name="a")).model_settings
    assert ms.frequency_penalty is None
    assert ms.presence_penalty is None
    assert ms.extra_body is None


def test_repetition_penalty_merges_with_reasoning_off_extra_body():
    # reasoning="none" on a vLLM model already sets extra_body (enable_thinking);
    # repetition_penalty must merge in, not clobber it.
    agent = _agent_for(
        AgentConfig(name="a", model="hosted_vllm/qwen3", repetition_penalty=1.1),
        reasoning_effort="none",
    )
    body = agent.model_settings.extra_body or {}
    assert body.get("repetition_penalty") == 1.1  # ty: ignore[unresolved-attribute]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}  # ty: ignore[not-subscriptable]


# ── #107 request hygiene: temperature / top_p / max_tokens passthrough ────────
# Per-preset sampling knobs (like opencode's per-model qwen tuning). None =
# don't send the param, so the server-side default wins — on vLLM that's the
# model's tuned generation_config.json values, which an explicit client 1.0
# would clobber.


def test_sampling_knobs_are_injected_into_model_settings():
    agent = _agent_for(AgentConfig(name="a", temperature=0.55, top_p=1.0, max_tokens=32000))
    ms = agent.model_settings
    assert ms.temperature == 0.55
    assert ms.top_p == 1.0
    assert ms.max_tokens == 32000


def test_unset_sampling_knobs_leave_model_settings_at_default():
    ms = _agent_for(AgentConfig(name="a")).model_settings
    assert ms.temperature is None
    assert ms.top_p is None
    assert ms.max_tokens is None


def test_sampling_knobs_survive_reasoning_effort_branches():
    # The three ModelSettings construction branches (reasoning off / effort
    # set / default) must all carry the knobs — a knob silently dropped on
    # one branch is exactly the kind of regression #107 hygiene guards.
    off = _agent_for(
        AgentConfig(name="a", model="hosted_vllm/glm-5.1", temperature=0.55),
        reasoning_effort="none",
    ).model_settings
    assert off.temperature == 0.55
    effort = _agent_for(
        AgentConfig(name="a", temperature=0.55), reasoning_effort="low"
    ).model_settings
    assert effort.temperature == 0.55
