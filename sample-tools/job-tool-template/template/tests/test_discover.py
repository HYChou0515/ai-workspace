"""Finding the backend: exactly one, or the tool refuses to start and says why."""

from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest
from backends import Minimal

from job_tool.discover import GROUP, NoBackend, load_backend


def _ep(name: str, value: str) -> EntryPoint:
    return EntryPoint(name=name, value=value, group=GROUP)


def test_the_one_registered_backend_is_the_backend():
    assert load_backend([_ep("acme", "backends:Minimal")]) is Minimal


def test_no_backend_is_refused_naming_where_to_register_one():
    with pytest.raises(NoBackend, match=GROUP):
        load_backend([])


def test_two_backends_are_refused_naming_both():
    # Picking one would make "which backend did that job go to?" unanswerable.
    with pytest.raises(NoBackend, match="acme.*other"):
        load_backend([_ep("acme", "backends:Minimal"), _ep("other", "backends:Full")])


def test_something_registered_that_is_not_a_backend_is_refused():
    with pytest.raises(NoBackend, match="backends:SPEC"):
        load_backend([_ep("acme", "backends:SPEC")])
