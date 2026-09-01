"""#624 P1: how much context we may actually use, and how much we are using.

Pure functions only — nothing here reaches the network or changes a turn. The
wiring lands in P2; keeping the arithmetic separate is deliberate, because
swapping the estimator alone (without deriving the budget from a real limit)
would make trimming 3.6x MORE aggressive on Chinese and worsen the very
forgetting this issue is about.
"""

from __future__ import annotations

from workspace_app.context_budget import (
    ContextLimit,
    catalog_limit,
    context_usage,
    estimate_messages,
    estimate_tokens,
    history_budget,
    resolve_context_limit,
)


class _Metrics:
    """Duck-typed stand-in for `resources.MessageMetrics` — the token counts the
    provider itself reported for one turn."""

    def __init__(self, prompt_tokens: int, completion_tokens: int = 0) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _Msg:
    """Duck-typed stand-in for `resources.Message`."""

    def __init__(
        self,
        content: str = "",
        tool_args: dict | None = None,
        *,
        role: str = "user",
        metrics: _Metrics | None = None,
        error_kind: str | None = None,
    ) -> None:
        self.content = content
        self.tool_args = tool_args
        self.role = role
        self.metrics = metrics
        self.error_kind = error_kind


# ── the limit ladder ────────────────────────────────────────────────


def test_config_wins_over_everything():
    """The operator's explicit value is the escape hatch — it outranks both
    what we learned and what the catalog claims."""
    got = resolve_context_limit(configured=8192, learned=40960, catalog=128000)
    assert got == ContextLimit(tokens=8192, source="config")


def test_learned_wins_over_catalog():
    """What the endpoint actually did beats what a table says it should do."""
    got = resolve_context_limit(configured=None, learned=4096, catalog=40960)
    assert got == ContextLimit(tokens=4096, source="learned")


def test_catalog_is_the_last_known_source():
    got = resolve_context_limit(configured=None, learned=None, catalog=40960)
    assert got == ContextLimit(tokens=40960, source="catalog")


def test_unknown_is_explicit_not_a_guess():
    """Nothing known ⇒ say so. A fabricated default is what #624 is about."""
    got = resolve_context_limit(configured=None, learned=None, catalog=None)
    assert got == ContextLimit(tokens=None, source="unknown")
    assert got.known is False


def test_nonpositive_values_are_treated_as_absent():
    """A 0 / negative from config or a catalog row is not a limit."""
    got = resolve_context_limit(configured=0, learned=-1, catalog=40960)
    assert got == ContextLimit(tokens=40960, source="catalog")


# ── the catalog lookup ──────────────────────────────────────────────


def test_catalog_knows_a_hosted_model():
    """The rung that answers without asking anyone: litellm's bundled table.

    It was `ollama/qwen3:14b` here, asserting 40,960 — which passed on a laptop
    with Ollama running and failed in CI, because that name is NOT in the table.
    litellm resolves it by asking the local Ollama DAEMON. So the "catalog"
    rung answers for a bundled local model only where one is already serving it,
    which is a different claim from the one this test was making, and not one a
    unit test can hold. `learned` and `probe` are the rungs that cover it.
    """
    assert catalog_limit("gpt-4o") == 128_000


def test_catalog_returns_none_for_a_self_hosted_name():
    """The production shape: OpenAI provider + custom endpoint + a model name
    no registry has ever heard of. Must answer "I don't know", not a default."""
    assert catalog_limit("openai/some-self-hosted-qwen") is None


def test_catalog_never_raises_on_a_junk_name():
    assert catalog_limit("") is None
    assert catalog_limit("!!! not a model !!!") is None


# ── counting ────────────────────────────────────────────────────────


def test_chinese_is_counted_near_one_token_per_char():
    """The whole point: `chars // 4` undercounts Chinese ~3.6x. A CJK char is
    roughly one token, so a 100-char Chinese string must land near 100, not 25."""
    text = "這批晶圓的量測資料我已經整理好了包含十二個站點的溫度壓力與流量記錄" * 3
    est = estimate_tokens(text)
    naive = len(text) // 4
    assert est > naive * 3
    assert 0.8 * len(text) <= est <= 1.2 * len(text)


def test_english_still_counts_about_four_chars_per_token():
    text = "the quick brown fox jumps over the lazy dog " * 10
    assert 0.8 * (len(text) / 4) <= estimate_tokens(text) <= 1.3 * (len(text) / 4)


def test_messages_include_tool_arguments():
    """Tool args ride the wire too — a big `patch` payload costs context."""
    plain = estimate_messages([_Msg(content="hello")])
    with_args = estimate_messages([_Msg(content="hello", tool_args={"q": "x" * 400})])
    assert with_args > plain + 50


def test_empty_messages_cost_nothing_dramatic():
    assert estimate_messages([]) == 0
    assert estimate_messages([_Msg(content="")]) >= 0


# ── the derived budget ──────────────────────────────────────────────


def test_unknown_limit_means_do_not_trim():
    """#624's locked decision: with no known ceiling we do NOT invent one and
    cut the user's memory — we send everything and learn from the response."""
    assert history_budget(ContextLimit(None, "unknown"), overhead_tokens=18_000) is None


def test_budget_subtracts_the_overhead_and_the_reply_reserve():
    """The system prompt + tool schemas are NOT free — today's budget ignores
    them entirely, which is how 18.5k + 24k could exceed a 40,960 model."""
    budget = history_budget(
        ContextLimit(40_960, "catalog"),
        overhead_tokens=18_000,
        reply_reserve=2_000,
        margin_ratio=0.0,
    )
    assert budget == 40_960 - 18_000 - 2_000


def test_margin_leaves_room_for_estimator_error():
    """The estimator is ~15% off, so the budget keeps a slice back rather than
    aiming exactly at the ceiling."""
    exact = history_budget(
        ContextLimit(40_960, "catalog"), overhead_tokens=0, reply_reserve=0, margin_ratio=0.0
    )
    with_margin = history_budget(
        ContextLimit(40_960, "catalog"), overhead_tokens=0, reply_reserve=0, margin_ratio=0.1
    )
    assert with_margin is not None and exact is not None
    assert with_margin < exact
    assert with_margin == int(40_960 * 0.9)


def test_overhead_larger_than_the_limit_yields_zero_not_negative():
    """A deploy whose system prompt alone exceeds the window: the budget floors
    at 0 (there is no room for history) rather than going negative."""
    budget = history_budget(
        ContextLimit(4_096, "learned"), overhead_tokens=18_000, reply_reserve=2_000
    )
    assert budget == 0


# ── what the thread is actually costing (#739 P1) ───────────────────


def test_usage_is_anchored_on_what_the_provider_reported():
    """The estimate is blind to the system prompt, the tool schemas and the
    skills index — every one of which the provider DID read. So the newest
    turn the provider measured is the baseline, and only what arrived after it
    is estimated.

    Its own answer is added from `completion_tokens`, because `prompt_tokens`
    is input only: the assistant's reply was not in the window it measured, but
    it will be in the next one."""
    msgs = [
        _Msg("舊的問題", role="user"),
        _Msg("舊的回答", role="assistant", metrics=_Metrics(9_000, completion_tokens=400)),
        _Msg("新的問題", role="user"),
    ]
    got = context_usage(msgs, limit=ContextLimit(tokens=40_960, source="catalog"))
    assert got.measured is True
    assert got.used == 9_000 + 400 + estimate_messages([_Msg("新的問題")])


def test_a_notice_costs_the_window_nothing():
    """`CONTEXT_NOTICE_ROLE` markers are user-facing only — `history_items`
    never replays them — so counting them would charge the user for text the
    model never receives, and the bar would creep up on its own."""
    msgs = [
        _Msg("回答", role="assistant", metrics=_Metrics(9_000, completion_tokens=400)),
        _Msg("這串對話有一段已經不再送給模型了。", role="notice"),
    ]
    got = context_usage(msgs, limit=ContextLimit(tokens=40_960, source="catalog"))
    assert got.used == 9_400


def test_a_failed_turn_costs_the_window_nothing():
    """Issue #37 markers (`error`) are human-only diagnostics — `history_items`
    keeps them OUT of the model's context by kind, so a thread full of
    connection failures must not read as a full window."""
    msgs = [
        _Msg("回答", role="assistant", metrics=_Metrics(9_000, completion_tokens=400)),
        _Msg("上游連線失敗", role="error", error_kind="error"),
    ]
    got = context_usage(msgs, limit=ContextLimit(tokens=40_960, source="catalog"))
    assert got.used == 9_400


def test_an_unknown_ceiling_has_no_denominator():
    """`resolve_context_limit` says `unknown` when nothing credible declared a
    ceiling. Rendering "9k / 40k" against a made-up 40k is exactly the disease
    #624 diagnosed: a number nobody measured, believed by everyone. The bar has
    to show the usage alone."""
    got = context_usage(
        [_Msg("回答", role="assistant", metrics=_Metrics(9_000))],
        limit=ContextLimit(tokens=None, source="unknown"),
    )
    assert got.used == 9_000
    assert got.limit is None
    assert got.ratio is None


def test_a_known_ceiling_gives_the_fraction_of_the_window_in_use():
    """What the bar fills to."""
    got = context_usage(
        [_Msg("回答", role="assistant", metrics=_Metrics(20_480))],
        limit=ContextLimit(tokens=40_960, source="catalog"),
    )
    assert got.ratio == 0.5


def test_a_turn_the_provider_never_measured_is_not_an_anchor():
    """Not every endpoint reports usage. A `prompt_tokens` of 0 is an ABSENT
    measurement, not a measured zero — anchoring on it would report a window
    that empties itself the moment one provider goes quiet."""
    msgs = [
        _Msg("回答", role="assistant", metrics=_Metrics(9_000, completion_tokens=400)),
        _Msg("再問", role="user"),
        _Msg("再答", role="assistant", metrics=_Metrics(0)),
    ]
    got = context_usage(msgs, limit=ContextLimit(tokens=40_960, source="catalog"))
    assert got.measured is True
    assert got.used == 9_400 + estimate_messages([_Msg("再問"), _Msg("再答")])


def test_a_thread_nobody_measured_yet_falls_back_to_the_estimate():
    """The first turn of a new chat has no reported usage to anchor on. It still
    needs a number — and `measured` says out loud that this one is a guess, so a
    caller never mistakes it for the provider's own count."""
    msgs = [_Msg("第一個問題", role="user")]
    got = context_usage(msgs, limit=ContextLimit(tokens=40_960, source="catalog"))
    assert got.measured is False
    assert got.used == estimate_messages(msgs)


def test_a_compacted_thread_stops_counting_what_it_compacted_away():
    """#739: after compaction the model is sent the summary and what followed
    it, so that is what the window holds. Keeping the old anchor would leave the
    gauge pinned at its pre-compaction figure — the user presses compact, the
    bar does not move, and the feature reads as broken.

    The measurement is also genuinely gone: the last reported `prompt_tokens`
    counted a request that included the span just replaced, so it is no longer
    an answer to "how full is the window". `measured` says so."""
    msgs = [
        _Msg("很久以前", role="user"),
        _Msg("回答", role="assistant", metrics=_Metrics(30_000, completion_tokens=400)),
        _Msg("先前:要修 X,試過 Y。", role="summary"),
        _Msg("接下來呢", role="user"),
    ]
    got = context_usage(msgs, limit=ContextLimit(tokens=40_960, source="catalog"))
    assert got.measured is False
    assert got.used == estimate_messages([_Msg("先前:要修 X,試過 Y。"), _Msg("接下來呢")])
