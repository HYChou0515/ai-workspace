"""#748 — what a reply's record is allowed to claim.

The turn's telemetry has two consumers with opposite requirements: the live
line a person reads (where an approximation beats a blank) and the record we
keep (where an approximation is a number nobody can tell apart from a
measurement). They shared one field, so the record inherited the guess.

These cover the record. The display fields are deliberately untouched — their
own tests still pin the fallback that keeps the line from reading `↑0 ↓0`.

`_TurnReducer` is underscore-named but it IS the seam: it is what turns a
turn's events into the rows that get persisted, so this is a test of what we
store, not of how the engine is wired.
"""

from __future__ import annotations

from workspace_app.api.events import AgentMetrics, MessageDelta
from workspace_app.api.turns import _TurnReducer


def _answered(*events) -> _TurnReducer:
    r = _TurnReducer()
    r.add(MessageDelta(text="hi"))
    for ev in events:
        r.add(ev)
    return r


def test_the_record_keeps_no_number_when_the_provider_reported_none():
    """The live fields still carry something to show; the record must not
    borrow it. `None` is the only honest answer to "how many tokens?" when the
    provider never said — and it is the one value a later reader cannot
    mistake for a measurement."""
    r = _answered(
        AgentMetrics(phase="final", prompt_tokens=120, completion_tokens=50, elapsed_ms=3400)
    )

    m = r.produced[-1].metrics
    assert m is not None
    assert m.prompt_tokens is None
    assert m.completion_tokens is None
    assert m.elapsed_ms == 3400  # the wall clock was measured, so it is kept


def test_the_record_keeps_the_providers_own_numbers_when_it_did_report():
    r = _answered(
        AgentMetrics(
            phase="final",
            prompt_tokens=120,
            completion_tokens=50,
            elapsed_ms=3400,
            measured_prompt_tokens=8412,
            measured_completion_tokens=356,
        )
    )

    m = r.produced[-1].metrics
    assert m is not None
    assert (m.prompt_tokens, m.completion_tokens) == (8412, 356)


def test_a_mid_stream_tick_does_not_overwrite_the_record():
    """`down` ticks carry approximations only. The reducer used to write the
    record on EVERY metrics event and was correct only because `final` happens
    to arrive last — a coincidence, not a design. Now that the record's fields
    are absent on a tick, relying on that ordering would blank a good record
    every 0.2 seconds."""
    r = _answered(
        AgentMetrics(
            phase="final",
            elapsed_ms=3400,
            measured_prompt_tokens=8412,
            measured_completion_tokens=356,
        ),
        AgentMetrics(phase="down", prompt_tokens=120, completion_tokens=50, elapsed_ms=3600),
    )

    m = r.produced[-1].metrics
    assert m is not None
    assert (m.prompt_tokens, m.completion_tokens) == (8412, 356)


def test_a_reported_zero_is_not_a_measurement():
    """Local Ollama routinely streams `usage` with zeros. Zero tokens is not a
    thing that happens — a reply exists, so something was read and something was
    written. `0` therefore means "this provider did not count", and recording it
    as a measured zero would poison every average computed over it."""
    from workspace_app.api.litellm_runner import _measured_tokens

    assert _measured_tokens(None) == (None, None)
    assert _measured_tokens((0, 0)) == (None, None)
    assert _measured_tokens((8412, 356)) == (8412, 356)
    assert _measured_tokens((8412, 0)) == (8412, None)  # per field, not all-or-nothing


# ── generation time: the denominator tok/s actually needs ─────────────────────


def test_generation_time_starts_at_the_first_token_not_at_the_request():
    """tok/s divided by the whole turn's wall clock is not a generation speed:
    it also carries TTFT, every tool call, every retry and every rate-limit
    hold. The worst part is TTFT — it grows with the prompt, so the SAME model
    looks slower as the conversation gets longer, which is exactly the reading
    a person is most likely to get wrong.

    The clock therefore runs from the first token to the last, and a turn with
    several round trips adds its stretches together. Where a stretch ends is
    TOLD to it, not guessed from the gap size — the runner already knows when
    the model stopped to call a tool, and a duration threshold would be a rule
    invented here that nothing else in the system agrees with.
    """
    from workspace_app.api.litellm_runner import _GenerationClock

    c = _GenerationClock(now=iter([10.0, 12.0, 20.0, 23.0]).__next__)
    c.token()  # first token at 10.0 — the 10s before it is TTFT, excluded
    c.token()  # 12.0
    c.pause()  # the model stopped to call a tool; the stretch closes here
    c.token()  # 20.0 — a new stretch; the 8s gap was the tool, not generation
    c.token()  # 23.0

    assert c.elapsed_ms() == 5000  # (12-10) + (23-20)


def test_generation_time_is_none_before_any_token_arrives():
    """A turn that fails before the model says anything generated nothing.
    Reporting 0 ms would divide into an infinite rate; `None` says what happened."""
    from workspace_app.api.litellm_runner import _GenerationClock

    assert _GenerationClock(now=lambda: 1.0).elapsed_ms() is None


def test_the_record_keeps_generation_time_apart_from_the_turn_clock():
    """Both numbers are real and they are not the same number. `elapsed_ms` is
    what the turn took (the UI's "· 12.3s"); `generation_ms` is what the model
    spent producing text, and only the second one is a sane denominator for
    tok/s. Storing one and deriving the other is how a field comes to mean two
    things (#739 §1.3)."""
    r = _answered(
        AgentMetrics(
            phase="final",
            elapsed_ms=61_000,  # a 60s tool ran in this turn
            generation_ms=5_000,
            measured_completion_tokens=356,
        )
    )

    m = r.produced[-1].metrics
    assert m is not None
    assert m.elapsed_ms == 61_000
    assert m.generation_ms == 5_000
    # 356/5s ≈ 71 tok/s, not 356/61s ≈ 6 — an order of magnitude apart.


# ── which model wrote this ───────────────────────────────────────────────────


def test_the_record_names_the_model_that_wrote_it():
    r = _answered(AgentMetrics(phase="final", elapsed_ms=3400, model="qwen3:14b"))

    m = r.produced[-1].metrics
    assert m is not None
    assert m.model == "qwen3:14b"


def test_the_effective_model_is_the_one_that_served_not_the_one_configured():
    """`_effective_model` is what the #69 trace already used, and it read the
    model off the agent's model object with the config as a fallback. Under
    failover that object is a FallbackModel, which had no `.model` — so it
    silently returned the CONFIGURED name, i.e. it was wrong in exactly the case
    where the two differ and the answer matters."""
    from workspace_app.api.litellm_runner import _effective_model

    class _Chain:
        served_model = "backup"

    class _Plain:
        model = "qwen3:14b"

    assert _effective_model(_Chain(), "primary") == "backup"  # served wins
    assert _effective_model(_Plain(), "primary") == "qwen3:14b"  # single endpoint
    assert _effective_model(None, "primary") == "primary"  # nothing else to go on
    # "" is neither a name nor the documented absence; an agent-config-less turn
    # used to persist it.
    assert _effective_model(None, "") is None

    class _NotYet:
        served_model = None

    # A chain that has not answered yet must not be reported as the head.
    assert _effective_model(_NotYet(), "primary") == "primary"


# ── asking the provider for its numbers at all ───────────────────────────────


def test_a_streamed_turn_asks_the_provider_to_report_usage():
    """Found by running it, not by reading it: the app never sent
    `stream_options`, and an OpenAI-compatible endpoint does not report usage on
    a stream unless asked. So on the streaming path — the default — the
    provider's real counts were never available at all, and litellm quietly
    supplied a figure from its OWN tokenizer instead, which is an estimate
    wearing a measurement's name.

    Recording `None` for that (P1) is honest but useless. Asking is what makes
    the column carry anything.

    Use the SDK's own `include_usage` knob. My first attempt put
    `stream_options` into `extra_args` by hand; the SDK passes that argument
    itself, so every turn died with "got multiple values for keyword argument
    'stream_options'" — and this test still passed, because it checked the dict
    we build rather than the call we make. The live turn is what caught it.
    """
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources import AgentConfig

    agent = _agent_for(AgentConfig(name="t", model="openai/x"))
    assert agent.model_settings.include_usage is True


# ── what the review found ────────────────────────────────────────────────────


def test_a_turn_that_never_reaches_final_still_records_what_it_measured():
    """Stop, MaxTurns, a provider error and the #113 repetition guard all end a
    turn without a `final` event. Recording only on `final` therefore threw away
    the elapsed and generation times of exactly the turns where "how long did it
    run" is the question — and those numbers were real. Before #748 the record
    survived; this keeps that."""
    r = _answered(
        AgentMetrics(
            phase="down",
            prompt_tokens=120,
            completion_tokens=50,
            elapsed_ms=42_000,
            generation_ms=1_200,
        )
    )

    m = r.produced[-1].metrics
    assert m is not None
    assert m.elapsed_ms == 42_000
    assert m.generation_ms == 1_200
    # Still no invented counts: a `down` tick carries no measurement.
    assert (m.prompt_tokens, m.completion_tokens) == (None, None)


def test_a_later_tick_never_erases_a_measurement_already_recorded():
    """The final event carries the provider's counts; a tick after it carries
    none. Overwriting wholesale would blank them."""
    r = _answered(
        AgentMetrics(
            phase="final",
            elapsed_ms=3400,
            measured_prompt_tokens=8412,
            measured_completion_tokens=356,
        ),
        AgentMetrics(phase="down", elapsed_ms=3600),
    )

    m = r.produced[-1].metrics
    assert m is not None
    assert (m.prompt_tokens, m.completion_tokens) == (8412, 356)
    assert m.elapsed_ms == 3600  # the newer clock reading is still taken


def test_one_token_is_not_a_measurable_generation_span():
    """A single delta gives one timestamp and therefore no duration. The old
    code answered that with `0` when the stretch was open and `None` when it had
    been banked — two answers to one question, and `0` divides into an infinite
    rate. Neither is a span, so both are None."""
    from workspace_app.api.litellm_runner import _GenerationClock

    open_stretch = _GenerationClock(now=iter([10.0]).__next__)
    open_stretch.token()
    assert open_stretch.elapsed_ms() is None

    banked = _GenerationClock(now=iter([10.0]).__next__)
    banked.token()
    banked.pause()
    assert banked.elapsed_ms() is None


def test_the_rate_counts_the_same_tokens_at_the_top_and_the_bottom():
    """The provider counts tool-call argument tokens in `completion_tokens`, so
    the clock must count their time — but the char count has to move with it or
    the mismatch just changes sign. The first fix moved only the clock, which
    turned a rate that was too high into one that was too low.

    `_delta_channel` is a catch-all `ignore`, so "everything not ignored" would
    also sweep in audio deltas (base64 bytes, in no token count at all). The
    predicate names what the provider bills for instead.
    """
    from workspace_app.api.litellm_runner import _is_generated_output

    assert _is_generated_output("response.output_text.delta") is True
    assert _is_generated_output("response.reasoning_text.delta") is True
    assert _is_generated_output("response.function_call_arguments.delta") is True
    # Not billed as completion tokens, and not text the model "wrote".
    assert _is_generated_output("response.audio.delta") is False
    assert _is_generated_output("response.completed") is False


def test_an_up_tick_cannot_zero_a_measured_turn_clock():
    """`phase="up"` carries `elapsed_ms=0` by construction — it is the START of
    an attempt, not a measurement of one. A retry re-emits it, and the merge left
    `elapsed_ms` unguarded, so a turn that had run 42s could be persisted as
    having taken 0ms while still reporting 1.2s of generation."""
    r = _answered(
        AgentMetrics(phase="down", elapsed_ms=42_000, generation_ms=1_200),
        AgentMetrics(phase="up", prompt_tokens=120, elapsed_ms=0),
    )

    m = r.produced[-1].metrics
    assert m is not None
    assert m.elapsed_ms == 42_000
    assert m.generation_ms == 1_200


def test_a_sub_millisecond_span_is_not_a_measurement():
    """The guard was on seconds, so a span under half a millisecond rounded to
    `0` — the exact value the record documents as impossible."""
    from workspace_app.api.litellm_runner import _GenerationClock

    c = _GenerationClock(now=iter([1.0, 1.0002]).__next__)
    c.token()
    c.token()
    c.pause()
    assert c.elapsed_ms() is None


def test_an_up_tick_creates_no_record_at_all():
    """The two guards on `up` do different jobs, and a probe that deleted only
    the phase check stayed green because the other one covered the overlap. This
    is the half only the phase check prevents: an `up` tick arriving before any
    measurement would otherwise mint a record whose every field is absent and
    whose elapsed is 0 — a message that claims to have been measured as nothing.
    """
    r = _TurnReducer()
    r.add(MessageDelta(text="hi"))
    r.add(AgentMetrics(phase="up", prompt_tokens=120, elapsed_ms=0))

    assert r.produced[-1].metrics is None
