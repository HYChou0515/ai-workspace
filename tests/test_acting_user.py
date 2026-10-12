"""`acting_user`: a route's declared identity, over the deploy's own reader."""

from __future__ import annotations

from workspace_app.acting_user import acting_as, honouring


def test_the_reader_answers_unless_a_route_declared_whom_it_acts_as():
    read = honouring(lambda: "from-cookie")

    assert read() == "from-cookie"
    with acting_as("alice"):
        assert read() == "alice"
        with acting_as("bob"):
            assert read() == "bob"
        assert read() == "alice"
    assert read() == "from-cookie"


def test_the_override_ends_with_its_block_even_when_the_block_raises():
    read = honouring(lambda: "from-cookie")
    try:
        with acting_as("alice"):
            raise RuntimeError
    except RuntimeError:
        pass
    assert read() == "from-cookie"


def test_wrapping_twice_is_wrapping_once():
    once = honouring(lambda: "x")
    assert honouring(once) is once
