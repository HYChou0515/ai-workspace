"""Ollama gets told how big a window to open.

Ollama does not open the model's window — it opens its own default (4,096) unless
the request asks for more, and it drops the overflow silently, from the FRONT. A
measured turn sent ~11,000 tokens (a 6.5k system prompt plus 4.5k of tool schemas)
and the endpoint reported reading exactly 4,096: the App's identity and its whole
instruction block were gone, leaving the tool inventory that sits at the end. The
model then answered fluently from a prompt it had never seen the start of.

The number to send is not a guess. `resolve_context_limit` already resolves this
endpoint's window every turn to size the history budget — the same figure the turn
was planned against. Sending anything else would mean budgeting for one window and
asking for another.

`num_ctx` must ride `extra_args` (a top-level completion kwarg): measured against
the daemon on an over-long prompt, top-level read 9,023 tokens where `extra_body`
still read 4,096. Same channel `think=False` already uses.
"""

from workspace_app.api.litellm_runner import _agent_for
from workspace_app.resources.agent_config import AgentConfig


def _extra_args(model: str, **kw) -> dict:
    ms = _agent_for(AgentConfig(name="a", model=model), **kw).model_settings
    return dict(ms.extra_args or {})


def test_ollama_is_told_the_resolved_window() -> None:
    assert _extra_args("ollama_chat/qwen3:14b", context_window=40960)["num_ctx"] == 40960


def test_the_ollama_prefix_is_also_covered() -> None:
    assert _extra_args("ollama/qwen3:14b", context_window=40960)["num_ctx"] == 40960


def test_a_non_ollama_endpoint_is_not_sent_num_ctx() -> None:
    """`num_ctx` is an Ollama option. vLLM / hosted endpoints size their own
    window, and an unknown kwarg is at best ignored and at worst a 400."""
    assert "num_ctx" not in _extra_args("hosted_vllm/qwen3", context_window=40960)
    assert "num_ctx" not in _extra_args("gpt-4o", context_window=40960)


def test_an_unresolved_window_leaves_the_endpoint_default_alone() -> None:
    """`None` means nothing could answer "how big is this endpoint" — a guess here
    would be the invented-constant defect #624 exists for."""
    assert "num_ctx" not in _extra_args("ollama_chat/qwen3:14b")
    assert "num_ctx" not in _extra_args("ollama_chat/qwen3:14b", context_window=None)


def test_num_ctx_does_not_clobber_the_reasoning_off_kwarg() -> None:
    """Reasoning-off on Ollama already rides `extra_args` as `think=False`; both
    have to survive."""
    args = _extra_args("ollama_chat/qwen3:14b", context_window=40960, reasoning_effort="none")
    assert args["num_ctx"] == 40960
    assert args["think"] is False


def test_num_ctx_reaches_the_reasoning_on_path_too() -> None:
    args = _extra_args("ollama_chat/qwen3:14b", context_window=40960, reasoning_effort="high")
    assert args["num_ctx"] == 40960


# ── the number itself: one resolution, two consumers ─────────────────


def _builder(*, configured=None, learned=None, catalog=None):
    """A TurnContextBuilder with only the ceiling knobs — `_context_window`
    needs no service bundle."""
    from workspace_app.api.turn_context import TurnContextBuilder

    b = TurnContextBuilder.__new__(TurnContextBuilder)
    b._context_limit = configured
    b.learned_limit_fn = (lambda model, base_url: learned) if learned else None
    b._catalog_cache = {}
    b._catalog_fn = lambda model: catalog
    return b


def _cfg(model: str = "ollama_chat/qwen3:14b") -> AgentConfig:
    return AgentConfig(name="a", model=model)


def test_the_window_comes_from_the_registry_when_nobody_overrides() -> None:
    assert _builder(catalog=40960)._context_window(_cfg()).tokens == 40960


def test_the_operator_override_outranks_the_registry() -> None:
    win = _builder(configured=8192, catalog=40960)._context_window(_cfg())
    assert (win.tokens, win.source) == (8192, "config")


def test_what_the_endpoint_taught_us_outranks_the_registry() -> None:
    win = _builder(learned=16384, catalog=40960)._context_window(_cfg())
    assert (win.tokens, win.source) == (16384, "learned")


def test_nothing_answers_stays_unknown_rather_than_guessing() -> None:
    """`unknown` must reach the runner as `None` so the endpoint keeps its own
    default — inventing a number here is the #624 defect."""
    win = _builder()._context_window(_cfg())
    assert (win.tokens, win.source) == (None, "unknown")
    assert "num_ctx" not in _extra_args("ollama_chat/qwen3:14b", context_window=win.tokens)


def test_no_agent_config_is_unknown_not_a_crash() -> None:
    assert _builder(catalog=40960)._context_window(None).tokens is None


# ── saying, once, what window we settled on ──────────────────────────


def _say(caplog, *calls) -> list[tuple[str, str]]:
    """Run `_agent_for` once per (model, kwargs) and return (level, message)."""
    from workspace_app.api import litellm_runner

    litellm_runner._NUM_CTX_SEEN.clear()
    caplog.clear()
    with caplog.at_level("INFO", logger=litellm_runner.__name__):
        for model, kw in calls:
            _agent_for(AgentConfig(name="a", model=model), **kw)
    return [(r.levelname, r.getMessage()) for r in caplog.records]


def test_the_chosen_window_is_logged(caplog) -> None:
    """The operator's first question is "so how big did it decide?" — answering
    only on failure leaves the working case unknowable."""
    said = _say(caplog, ("ollama_chat/qwen3:14b", {"context_window": 40960}))
    assert len(said) == 1
    level, msg = said[0]
    assert level == "INFO"
    assert "40960" in msg and "qwen3:14b" in msg


def test_the_same_decision_is_not_repeated_every_turn(caplog) -> None:
    said = _say(caplog, *[("ollama_chat/qwen3:14b", {"context_window": 40960})] * 4)
    assert len(said) == 1


def test_a_changed_window_is_stated_again(caplog) -> None:
    """Keyed on the decision, not the model: when a later turn resolves a
    different window (the learner supplying what a rejection taught it), staying
    quiet would hide the change behind the first turn's line."""
    said = _say(
        caplog,
        ("ollama_chat/qwen3:14b", {"context_window": 40960}),
        ("ollama_chat/qwen3:14b", {"context_window": 8192}),
    )
    assert len(said) == 2
    assert "8192" in said[1][1]


def test_an_ollama_endpoint_of_unknown_size_is_warned_about(caplog) -> None:
    """The one case that still truncates in silence: we are on Ollama, so the
    endpoint WILL impose a window, and we could not find out how big to ask for.
    Left unsaid it looks exactly like a model that stopped following its prompt."""
    said = _say(caplog, ("ollama_chat/qwen3-custom", {}))
    assert len(said) == 1
    level, msg = said[0]
    assert level == "WARNING"
    assert "num_ctx" in msg and "qwen3-custom" in msg


def test_a_non_ollama_endpoint_says_nothing(caplog) -> None:
    """vLLM and hosted endpoints size their own window — `num_ctx` is not theirs
    to receive, so there is no decision to report on any turn."""
    assert _say(caplog, ("hosted_vllm/qwen3", {"context_window": 40960})) == []
    assert _say(caplog, ("gpt-4o", {})) == []
