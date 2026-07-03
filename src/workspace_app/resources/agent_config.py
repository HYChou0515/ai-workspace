from msgspec import Struct, field


class Suggestion(Struct):
    """One quick-prompt chip in the agent panel.

    ``label`` is the short text the button shows. ``prompt`` is what gets
    sent verbatim as the user message when the chip is pressed. Split so
    a chip can read as "SPC" while submitting the full
    "Show me the SPC analysis with control charts and explain..." (#91).
    """

    label: str
    prompt: str


class AgentConfig(Struct):
    name: str
    model: str = "ollama_chat/qwen3:14b"
    system_prompt: str = ""
    description: str = ""
    """One-line picker blurb — the composer model picker renders it under
    the entry name (handoff redesign). "" = no note shown."""

    suggestions: list[Suggestion] = field(default_factory=list)
    """Quick-prompt chips shown in the agent panel. Each entry has a
    ``label`` (button text) and a ``prompt`` (sent verbatim as the user
    message when the chip is pressed). The prompt library lives with the
    agent config, not hardcoded in the FE."""

    allowed_tools: list[str] | None = None
    """Three states (Q4-followup of the config grill):

    - ``None`` — "I haven't specified"; the runner exposes its default
      workspace toolset. Bare ``AgentConfig(name=...)`` defaults here,
      and bundled RCA presets carry it (so picking one yields the
      standard agent).
    - ``[]``    — "explicitly zero tools"; the runner exposes nothing.
      This is what catches the KB-chat-pointed-at-RCA-preset footgun:
      a preset that doesn't actually grant `kb_search` resolves with
      `[]` and the catalog validator surfaces it loud.
    - ``[...]`` — exactly these.

    The runner-side fix (``litellm_runner._agent_for``) stops aliasing
    ``[]`` to ``None`` so the three states stay distinguishable."""

    env: dict[str, str] = field(default_factory=dict)
    sandbox_image: str = "workspace-app/sandbox:py312-ds"
    """Default sandbox image built from `docker/Dockerfile.workspace`
    (plan-backend §7.5). Bumped from the prior workspace-app default of
    `python:3.12-slim` to one with ipykernel + numpy/pandas/matplotlib/scipy
    pre-installed."""

    idle_timeout_seconds: int = 28800
    """8 hours — per grill-me Q10 the RCA workflow expects long
    open-then-come-back sessions. Was 900 (15 min) for workspace-app."""

    llm_base_url: str = ""
    """Per-config LLM endpoint base URL. ``""`` falls back to the
    runner's constructor default (set from top-level ``Settings.llm``)
    so a deploy that uses a single endpoint everywhere doesn't need
    to set this per-preset. The new config schema's
    ``agents.presets.<name>.llm.base_url`` populates this at catalog
    resolution; the runner consults it per turn."""

    llm_api_key: str = ""
    """Per-config LLM API key. Same fallback as ``llm_base_url`` —
    empty means "use the runner's constructor default"."""

    frequency_penalty: float | None = None
    """#113 Layer 1 (anti-repetition). OpenAI-style: >0 discourages tokens by
    how often they've appeared. ``None`` = inherit the model default (don't
    send the param). Honoured by vLLM; **silently dropped by Ollama's newer Go
    runner** — which is why the stream-side repetition guard, not this, is the
    real backstop."""

    presence_penalty: float | None = None
    """#113 Layer 1. OpenAI-style: >0 discourages tokens that have appeared at
    all (regardless of count). ``None`` = inherit. Same Ollama caveat as
    ``frequency_penalty``."""

    repetition_penalty: float | None = None
    """#113 Layer 1. Non-standard (HF/vLLM): >1 divides the logit of seen
    tokens. Sent via ``extra_body`` (litellm forwards it). ``None`` = inherit.
    Same Ollama caveat. Don't crank it on reasoning models — it can *cause*
    loops; prefer modest values (~1.05–1.1)."""

    temperature: float | None = None
    """#107 request hygiene. ``None`` = don't send the param, so the
    server-side default wins — on vLLM that's the model's tuned
    ``generation_config.json`` values, which an explicit client 1.0 would
    clobber. Set only to pin a specific value (opencode pins 0.55 for its
    qwen-family entries)."""

    top_p: float | None = None
    """#107 request hygiene. Same ``None``-inherits contract as
    ``temperature``."""

    max_tokens: int | None = None
    """#107 request hygiene. ``None`` = omit — vLLM then grants the full
    remaining context, which is the SAFE choice for long tool args (a
    smallish explicit cap truncates the call mid-JSON and turns every long
    write into a parse failure). Set a large value (opencode sends
    min(model limit, 32000)) only to bound runaway generations."""
