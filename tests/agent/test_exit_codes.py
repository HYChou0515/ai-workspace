"""What an exit code means, in words the model can act on (#674).

A number is not guidance. `exit_code=-9` tells a model nothing, and it is what
a tool gets for exceeding a memory limit *we* set — the single most confusing
failure this platform can produce. Every code below either changes what the
model should do next, or names a cause nobody could infer from the output.
"""

from __future__ import annotations

import pytest

from workspace_app.agent.exit_codes import explain


def test_success_and_plain_failure_say_nothing():
    # Silence is the right answer when the output already carries it. A note on
    # every failure is noise, and noise on every turn is expensive.
    assert explain(0) is None
    assert explain(1) is None


def test_a_retryable_failure_tells_the_model_it_may_try_again():
    note = explain(2)

    assert note is not None
    assert "again" in note.lower()


def test_a_blocked_failure_sends_the_model_to_the_person_who_can_unblock_it():
    # The distinction that earns this code: nobody gains from retrying, and
    # there IS something a human can do — so the model must stop and say what.
    note = explain(3)

    assert note is not None
    assert "again" not in note.lower().split("calling")[0]
    assert "env" in note.lower() or "variable" in note.lower()


def test_a_timeout_says_the_platform_stopped_it():
    note = explain(124)

    assert note is not None and "time" in note.lower()


@pytest.mark.parametrize("code", [-9, 137])
def test_a_killed_process_points_at_the_limit_that_killed_it(code: int):
    """Both spellings of "killed by SIGKILL": the host returns the raw negative
    returncode, other backends report 128+signal. A tool author reading either
    one needs the same sentence."""
    note = explain(code)

    assert note is not None
    assert "memor" in note.lower()


@pytest.mark.parametrize("code", [-11, 139])
def test_a_crash_names_the_cause_the_author_cannot_see(code: int):
    # A segfault in a bundle means it was built for a different environment —
    # the exact failure the builder image exists to prevent. Saying so is how
    # anyone finds out the gate leaked.
    note = explain(code)

    assert note is not None
    assert "built" in note.lower()


def test_a_launcher_that_cannot_run_is_reported_as_a_broken_bundle():
    # 126/127 come from the shell, and mean the tool never started at all.
    # Handed to a model bare, they look like the tool ran and failed.
    assert "bundle" in (explain(126) or "").lower()
    assert "bundle" in (explain(127) or "").lower()


@pytest.mark.parametrize("code", [-6, 134])
def test_a_signal_without_a_sentence_still_says_it_was_a_signal(code: int):
    """A C library calling abort(), a SIGTERM during a drain — codes we have no
    specific advice for. Saying "signal 6" beats a bare number, and beats
    inventing advice we do not have."""
    note = explain(code)

    assert note is not None
    assert "signal 6" in note


def test_an_ordinary_high_exit_code_is_not_mistaken_for_a_signal():
    # 128 + N only means a signal above 128; a tool returning 130 of its own
    # accord would otherwise be reported as "killed", which is a lie.
    assert explain(200) is None


def test_the_memory_kill_names_the_setting_a_person_can_now_change():
    """The debt §1.7 of the per-item resources plan took on.

    That plan refused to invent a minimum environment size, on the explicit
    grounds that "whoever sets it low will notice" — which only holds if the
    failure points back at the setting. It cannot point at itself: a process
    killed by the memory cgroup exits with SIGKILL and no output, which is why
    this sentence exists at all.

    Before per-item sizing the only remedy was "use less data", because the
    limit belonged to the App and nobody in the conversation could move it. Now
    somebody may have typed the number themselves, and sending them to shrink
    their data instead sends them down the path where the product simply looks
    broken.
    """
    import signal

    from workspace_app.agent.exit_codes import explain

    note = explain(128 + int(signal.SIGKILL)) or ""

    assert "memory" in note.lower(), "still says what killed it"
    # …and now also where that ceiling comes from, for the case where the person
    # reading this is the one who set it.
    assert "environment" in note.lower()
