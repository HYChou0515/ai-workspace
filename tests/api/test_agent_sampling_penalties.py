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
    assert (ms.extra_body or {}).get("repetition_penalty") == 1.1


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
    assert body.get("repetition_penalty") == 1.1
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
