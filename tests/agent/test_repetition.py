"""RepetitionDetector — mid-stream neural-text-degeneration guard (#113).

Detects when a model's output (or reasoning) tail degenerates into a repeated
block — `the the the`, or a repeated multi-sentence chunk — so the turn can be
stopped gracefully and the persisted text truncated to before the loop.
"""

from workspace_app.agent.repetition import RepetitionDetector


def test_block_repeated_n_times_at_tail_is_flagged():
    d = RepetitionDetector(repeats=3)
    # "abc" repeated 4 times — a tail of period 3, well past the loop floor.
    assert d.feed("abcabcabcabc") is not None


def test_loop_length_covers_the_run_keeping_clean_prefix():
    d = RepetitionDetector(repeats=3)
    # Clean prefix "Hello. " then "ha" looping many times.
    text = "Hello. " + "ha" * 6
    result = d.feed(text)
    assert result is not None
    assert text[: len(text) - result.loop_length] == "Hello. "


def test_window_bounds_memory_yet_a_fresh_loop_still_fires():
    # A small window: lots of clean text scrolls off, then a loop appears at the
    # live tail and is still caught (the window only bounds memory, not detection).
    d = RepetitionDetector(repeats=3, window=64)
    filler = "".join(f"step {i} done; " for i in range(40))  # non-periodic, >> window
    assert d.feed(filler) is None
    assert d.feed("loopy " * 3) is not None


def test_reset_clears_state_so_repeats_across_responses_do_not_accumulate():
    d = RepetitionDetector(repeats=3)
    block = "讓我檢查foo,現在有遇到問題xxx,"
    # Same block emitted once per response, with a reset (a tool-call boundary)
    # in between — this is cross-step repetition (case 2), out of scope.
    for _ in range(3):
        assert d.feed(block) is None
        d.reset()


def test_repetition_inside_a_fenced_code_block_is_ignored():
    d = RepetitionDetector(repeats=3)
    # Inside a ``` fence, a repeated line is legit code, not degeneration.
    fired = None
    for ch in "see:\n```\nx = 1\nx = 1\nx = 1\nx = 1\n":
        fired = fired or d.feed(ch)
    assert fired is None


def test_repeated_multi_sentence_cjk_block_is_flagged():
    d = RepetitionDetector(repeats=3)
    block = "讓我檢查foo,現在有遇到問題xxx,"
    result = None
    for _ in range(3):
        result = d.feed(block)
    assert result is not None
    assert result.loop_length == len(block) * 3


def test_normal_prose_and_lists_do_not_trigger():
    d = RepetitionDetector(repeats=3)
    text = (
        "Here is the analysis. First, the disk filled up. "
        "1. check logs\n2. rotate them\n3. add alerting\n"
        "These three steps should resolve the incident cleanly."
    )
    fired = None
    for ch in text:
        fired = fired or d.feed(ch)
    assert fired is None


def test_short_punctuation_runs_do_not_trigger_but_long_char_loop_does():
    # `---`, `...`, `!!!` are markdown / punctuation, not degeneration.
    for punct in ("---", "...", "!!!", "==="):
        d = RepetitionDetector(repeats=3)
        fired = None
        for ch in f"see below {punct}":  # fed live: punct is briefly the tail
            fired = fired or d.feed(ch)
        assert fired is None, punct
    # A genuine single-char loop (dozens of chars) is degeneration.
    d = RepetitionDetector(repeats=3)
    fired = None
    for ch in "loading" + "a" * 20:
        fired = fired or d.feed(ch)
    assert fired is not None
